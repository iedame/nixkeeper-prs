"""`python3 -m nixkeeper_prs [DATA_DIR]` (or `nix run . -- data`): write the
digest of nixpkgs' open PRs to DATA_DIR (default data/):

    prs.json     every open PR with what it is and where it stands
                 (facts.analyse), and the groups of duplicates
    diffs.json   each PR's diff fingerprint, by the head commit it was read
                 at: read again only when the PR changes
    meta.json    when, how many, and how many of each kind

The PRs' details: a full sweep the first time and every FULL_EVERY (about
an hour: GitHub takes seconds a page), else the last digest's, with the
details of those changed since read again (a light list of every open PR,
minutes). Diffs are read through GitHub's REST API one a second, at most MAX_DIFFS
and MAX_MINUTES after the PRs are known (the first runs read the backlog),
small PRs only."""

import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta

from . import facts, fetch, github, sources

FORMAT = 1
MAX_DIFFS = 1500
MAX_MINUTES = 40
MAX_FAILURES_IN_A_ROW = 10
# Diffs worth fingerprinting: not the huge ones (lock files, treewide edits),
# which are never copies of another's.
MAX_DIFF_FILES = 50
MAX_DIFF_LINES = 2000
# How often the details of every PR are read again: what doesn't move a PR's
# last update (its checks finishing, master moving under it) is caught up then.
FULL_EVERY = timedelta(hours=24)
# A PR's own fields (github.pr's), kept from one run to the next; the rest is
# worked out again each run (facts.analyse).
RAW = (
    "n",
    "title",
    "author",
    "draft",
    "created",
    "updated",
    "base",
    "head",
    "additions",
    "deletions",
    "changedFiles",
    "files",
    "labels",
    "mergeable",
    "review",
    "approvedBy",
    "ci",
)
# Read again even when not updated: GitHub hadn't worked out its merge state,
# or its checks were still running.
UNSETTLED = {"mergeable": {"UNKNOWN"}, "ci": {"PENDING", "EXPECTED"}}


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def write_json(path, data, indent=None):
    with open(path, "w") as f:
        json.dump(data, f, indent=indent, sort_keys=True, separators=None)
        f.write("\n")


def read_diffs(prs, cache, started, tok):
    """Fingerprint the diffs of the PRs not read at their head commit yet
    (cache: {number: [head, fingerprint]}, updated in place; PRs no longer
    open dropped), the newest first, within MAX_DIFFS and MAX_MINUTES.
    Returns how many were read and are still to read."""
    open_now = {str(pr["n"]) for pr in prs}
    for n in set(cache) - open_now:
        del cache[n]
    wanted = [
        pr
        for pr in sorted(prs, key=lambda p: p["updated"], reverse=True)
        if pr["changedFiles"] <= MAX_DIFF_FILES
        and pr["additions"] + pr["deletions"] <= MAX_DIFF_LINES
        and (cache.get(str(pr["n"])) or [None])[0] != pr["head"]
    ]
    read = in_a_row = 0
    for pr in wanted[:MAX_DIFFS]:
        if time.monotonic() - started > MAX_MINUTES * 60:
            break
        if in_a_row >= MAX_FAILURES_IN_A_ROW:
            print("::warning::GitHub stopped answering for diffs.", file=sys.stderr)
            break
        try:
            body = github.diff(pr["n"], tok)
            cache[str(pr["n"])] = [pr["head"], github.diff_hash(body)]
        except fetch.RateLimited as e:
            print(f"::warning::{e}: the rest wait for the next run.", file=sys.stderr)
            break
        except OSError as e:
            print(f"  #{pr['n']}: {e}", file=sys.stderr)
            in_a_row += 1
            continue
        in_a_row = 0
        read += 1
    return read, len(wanted) - read


def unsettled(pr):
    return any(pr.get(k) in values for k, values in UNSETTLED.items())


def sweep(previous, swept_at, now, tok):
    """Every open PR's details (github.pr's), oldest first, with how they
    were read ("full" or "changed") and how many GitHub says are open:
    a full sweep without a last digest or when its full sweep is
    FULL_EVERY old; else the last digest's PRs still open, the changed ones
    (updated since, new, unsettled) read again."""
    if (
        not previous
        or not swept_at
        or now - datetime.fromisoformat(swept_at) >= FULL_EVERY
    ):
        prs, total = github.open_prs(tok)
        return prs, "full", total
    listed = github.open_list(tok)
    before = {pr["n"]: {k: pr[k] for k in RAW if k in pr} for pr in previous}
    changed = [
        n
        for n, updated in listed.items()
        if n not in before or before[n]["updated"] != updated or unsettled(before[n])
    ]
    print(f"  {len(changed):,} of {len(listed):,} changed since", file=sys.stderr)
    fresh = {pr["n"]: pr for pr in github.details(changed, tok)}
    prs = [fresh.get(n) or before[n] for n in listed if n in fresh or n in before]
    prs.sort(key=lambda pr: pr["created"])
    return prs, "changed", len(listed)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    directory = argv[0] if argv else "data"
    os.makedirs(directory, exist_ok=True)
    started = time.monotonic()
    now = datetime.now(UTC).isoformat(timespec="seconds")

    last = read_json(os.path.join(directory, "prs.json"), {})
    last_meta = read_json(os.path.join(directory, "meta.json"), {})
    print("Reading nixpkgs' open PRs...", file=sys.stderr)
    tok = fetch.token()
    prs, how, total = sweep(
        last.get("prs"), last_meta.get("fullSweepAt"), datetime.fromisoformat(now), tok
    )
    swept_at = now if how == "full" else last_meta.get("fullSweepAt")
    print(
        f"  {len(prs):,} PRs ({how}; GitHub says {total:,} open), in "
        f"{(time.monotonic() - started) / 60:.1f} min",
        file=sys.stderr,
    )
    print(
        "Reading the channel's index, master's versions, the bot's queue...",
        file=sys.stderr,
    )
    index = sources.channel()
    master = sources.master(index)
    queue = sources.queue()

    cache = read_json(os.path.join(directory, "diffs.json"), {})
    # The diffs' time starts now, whatever the PRs took.
    read, pending = read_diffs(prs, cache, time.monotonic(), tok)
    hashes = {int(n): h for n, (head, h) in cache.items()}
    prs, groups = facts.analyse(prs, index, master, queue, hashes)

    # When the digest is written (now: when the PRs were read, fullSweepAt's).
    written = datetime.now(UTC).isoformat(timespec="seconds")
    counts = Counter(b for pr in prs for b in pr["buckets"])
    states = Counter(pr["state"] for pr in prs if pr.get("state"))
    meta = {
        "format": FORMAT,
        "generatedAt": written,
        "fullSweepAt": swept_at,
        "sweep": how,
        "prs": len(prs),
        "buckets": dict(counts.most_common()),
        "states": dict(states.most_common()),
        "blocksBot": sum(1 for pr in prs if pr.get("blocksBot")),
        "mergeBot": {
            "eligible": sum(1 for pr in prs if pr.get("mergeBot")),
            "ready": sum(1 for pr in prs if (pr.get("mergeBot") or {}).get("ready")),
        },
        "groups": dict(Counter(g["kind"] for g in groups)),
        "diffs": {"known": len(cache), "readNow": read, "pending": pending},
    }
    write_json(os.path.join(directory, "diffs.json"), cache)
    write_json(
        os.path.join(directory, "prs.json"),
        {"format": FORMAT, "generatedAt": written, "prs": prs, "groups": groups},
    )
    write_json(os.path.join(directory, "meta.json"), meta, indent=2)
    print(json.dumps(meta, indent=2))
    return 0
