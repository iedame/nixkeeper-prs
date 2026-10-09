"""`python3 -m nixkeeper_prs [DATA_DIR]` (or `nix run . -- data`): write the
digest of nixpkgs' open PRs to DATA_DIR (default data/):

    prs.json     every open PR with what it is and where it stands
                 (facts.analyse), and the groups of duplicates
    diffs.json   each PR's diff fingerprint and facts (diffs.py), by the head
                 commit it was read at: read again only when the PR changes
    issues.json  every open issue's number and title (nixkeeper counts a
                 package's issues by them)
    merged.json  the PRs merged into master since the nixos-unstable
                 channel's commit (what master has that the channel doesn't)
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

from . import diffs, facts, fetch, github, sources

FORMAT = 1
MAX_DIFFS = 1500
MAX_MINUTES = 40
MAX_FAILURES_IN_A_ROW = 10
# Diffs worth fingerprinting: not the huge ones (lock files, treewide edits),
# which are never copies of another's.
MAX_DIFF_FILES = 50
MAX_DIFF_LINES = 2000
# How often the details of every PR are read again: what doesn't move a PR's
# last update (its checks finishing, master moving under it, GitHub working
# out its merge state) is caught up then. Between full sweeps only new and
# updated PRs are read: most PRs' merge state is UNKNOWN in bulk answers
# (GitHub works it out lazily: 9,800 of 12,440 on 2026-10-09) and thousands
# keep a pending check for days, so re-reading the unsettled re-read nearly
# everything every run.
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


def read_at(entry, head):
    """Whether a cache entry is the diff at head, with its facts."""
    return bool(entry) and len(entry) >= 3 and entry[0] == head


def read_diffs(prs, cache, started, tok):
    """Read the diffs of the PRs not read at their head commit yet (cache:
    {number: [head, fingerprint, facts]}, updated in place; PRs no longer
    open dropped; entries from before facts were kept, read again), the
    newest first, within MAX_DIFFS and MAX_MINUTES.
    Returns how many were read and are still to read."""
    open_now = {str(pr["n"]) for pr in prs}
    for n in set(cache) - open_now:
        del cache[n]
    wanted = [
        pr
        for pr in sorted(prs, key=lambda p: p["updated"], reverse=True)
        if pr["changedFiles"] <= MAX_DIFF_FILES
        and pr["additions"] + pr["deletions"] <= MAX_DIFF_LINES
        and not read_at(cache.get(str(pr["n"])), pr["head"])
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
            cache[str(pr["n"])] = [
                pr["head"],
                github.diff_hash(body),
                diffs.facts(body),
            ]
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


def sweep(previous, swept_at, now, tok):
    """Every open PR's details (github.pr's), oldest first, with how they
    were read ("full" or "changed") and how many GitHub says are open:
    a full sweep without a last digest or when its full sweep is
    FULL_EVERY old; else the last digest's PRs still open, the changed ones
    (new, or updated since) read again."""
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
        if n not in before or before[n]["updated"] != updated
    ]
    print(f"  {len(changed):,} of {len(listed):,} changed since", file=sys.stderr)
    fresh = {pr["n"]: pr for pr in github.details(changed, tok)}
    prs = [fresh.get(n) or before[n] for n in listed if n in fresh or n in before]
    prs.sort(key=lambda pr: pr["created"])
    return prs, "changed", len(listed)


def list_issues(directory, tok, now):
    """Write issues.json (every open issue); on failure keep the last one.
    Returns meta's "issues" ({"count", "at"}: the last listing that worked)."""
    path = os.path.join(directory, "issues.json")
    try:
        issues = github.open_issues(tok)
    except (OSError, ValueError, KeyError) as e:
        print(
            f"::warning::Listing open issues failed ({e}): the last kept.",
            file=sys.stderr,
        )
        last = read_json(path, {})
        return {"count": len(last.get("issues") or []), "at": last.get("generatedAt")}
    write_json(path, {"format": FORMAT, "generatedAt": now, "issues": issues})
    return {"count": len(issues), "at": now}


def list_merged(directory, tok, now):
    """Write merged.json (PRs merged into master since the channel's
    commit); on failure keep the last one. Returns meta's "merged"
    ({"count", "at", "revision", "since"})."""
    path = os.path.join(directory, "merged.json")
    try:
        revision = sources.channel_revision()
        since = github.commit_date(revision, tok)
        if not since:
            raise OSError(f"no commit date for the channel's revision {revision}")
        merged = github.merged_since(
            datetime.fromisoformat(since.replace("Z", "+00:00")),
            datetime.fromisoformat(now),
            tok,
        )
    except (OSError, ValueError, KeyError) as e:
        print(
            f"::warning::Listing merged PRs failed ({e}): the last kept.",
            file=sys.stderr,
        )
        last = read_json(path, {})
        return {
            "count": len(last.get("prs") or []),
            "at": last.get("generatedAt"),
            "revision": last.get("revision"),
            "since": last.get("since"),
        }
    write_json(
        path,
        {
            "format": FORMAT,
            "generatedAt": now,
            "revision": revision,
            "since": since,
            "prs": merged,
        },
    )
    return {"count": len(merged), "at": now, "revision": revision, "since": since}


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
    print(
        "Listing open issues and PRs merged since the channel's commit...",
        file=sys.stderr,
    )
    issues_meta = list_issues(directory, tok, now)
    merged_meta = list_merged(directory, tok, now)

    cache = read_json(os.path.join(directory, "diffs.json"), {})
    # The diffs' time starts now, whatever the PRs took.
    read, pending = read_diffs(prs, cache, time.monotonic(), tok)
    # Each PR's fingerprint and facts, at the head read (a PR changed since
    # keeps the last read's until its diff is read again).
    known = {
        int(n): (entry[1], entry[2] if len(entry) >= 3 else {})
        for n, entry in cache.items()
    }
    prs, groups = facts.analyse(prs, index, master, queue, known)

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
        "issues": issues_meta,
        "merged": merged_meta,
    }
    write_json(os.path.join(directory, "diffs.json"), cache)
    write_json(
        os.path.join(directory, "prs.json"),
        {"format": FORMAT, "generatedAt": written, "prs": prs, "groups": groups},
    )
    write_json(os.path.join(directory, "meta.json"), meta, indent=2)
    print(json.dumps(meta, indent=2))
    return 0
