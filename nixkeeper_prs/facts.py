"""What each open PR is and where it stands, from its details and what
nixpkgs has: buckets (what kind of change), an update's state against the
channel and master (superseded, overtaken, backwards), whether it blocks
the update bot, whether a maintainer can merge it through the merge bot,
and the PRs it duplicates.

Versions are ordered as nixkeeper orders them (its versions.py: Repology's
libversion, with nixpkgs' "unstable")."""

import re

from nixkeeper.versions import is_newer

from .sources import BY_NAME, canonical

BOT = "r-ryantm"
# nixpkgs' title conventions (CONTRIBUTING.md): "attr: 1.0 -> 1.1",
# "attr: init at 1.0", "attr: drop", "attr: remove".
UPDATE = re.compile(r"^([\w.+-]+): (\S+) (?:->|→) (\S+)\s*$")
INIT = re.compile(r"^([\w.+-]+): init at \S+", re.IGNORECASE)
DROP = re.compile(r"^([\w.+-]+): (?:drop|remove)\b", re.IGNORECASE)
# How small a change is "tiny": lines added and removed, and files.
TINY_LINES = 10
TINY_FILES = 2
# Ecosystems by where their packages live.
ECOSYSTEMS = (
    ("python", re.compile(r"^pkgs/development/python-modules/|^pkgs/.*python")),
    ("haskell", re.compile(r"^pkgs/development/haskell-modules/")),
    ("node", re.compile(r"^pkgs/development/node-packages/")),
    ("nixos", re.compile(r"^nixos/")),
    ("lib", re.compile(r"^lib/")),
    ("ci", re.compile(r"^ci/|^\.github/")),
    ("docs", re.compile(r"^doc/|\.md$")),
)
# The merge bot merges into nixpkgs' development branches.
DEVELOPMENT = {"master", "staging", "staging-next"}


def title_parts(title):
    """(kind, attr, from, to) of a conventional title: update, init or drop
    (from/to None but for updates); (None, ...) for any other."""
    if m := UPDATE.match(title):
        return "update", m.group(1), m.group(2), m.group(3)
    if m := INIT.match(title):
        return "init", m.group(1), None, None
    if m := DROP.match(title):
        return "drop", m.group(1), None, None
    return None, None, None, None


def by_name_packages(files):
    """The pkgs/by-name packages a PR touches, and whether it touches
    nothing else (the merge bot's first rule)."""
    names = sorted({m.group(1) for f in files if (m := BY_NAME.match(f))})
    only = bool(files) and all(BY_NAME.match(f) for f in files)
    return names, only


def buckets(pr, kind, packages, only_by_name):
    """What kind of change: the title's kind, by-name only, tiny, the bot's,
    ecosystems by path."""
    found = [kind] if kind else []
    if only_by_name:
        found.append("by-name")
    if (
        pr["additions"] + pr["deletions"] <= TINY_LINES
        and pr["changedFiles"] <= TINY_FILES
    ):
        found.append("tiny")
    if pr["author"] == BOT:
        found.append("bot")
    for name, pattern in ECOSYSTEMS:
        if any(pattern.search(f) for f in pr["files"]):
            found.append(name)
    if pr["changedFiles"] >= 50:
        found.append("treewide")
    return found


def update_state(frm, to, now):
    """Where an update ("frm -> to") stands against what nixpkgs has now
    (now: master's version, else the channel's): "superseded" (nixpkgs
    moved past frm and has to, or newer: close it), "overtaken" (nixpkgs
    moved past frm, not up to to: rebase), "backwards" (to sorts below frm:
    a downgrade or a version scheme change), or None."""
    if is_newer(frm, to):
        return "backwards"
    if not now or now == frm:
        return None
    if not is_newer(to, now):
        return "superseded"
    if is_newer(now, frm):
        return "overtaken"
    return None


def blocks_bot(pr, attr, frm, to, queue):
    """When the update bot would make this very update next (its queue) and
    this PR, not the bot's own, has its title: the bot then finds it and
    skips the update ("There might already be an open PR"). {"title", "by":
    the day the bot is expected to try} or None."""
    if pr["author"] == BOT or not attr:
        return None
    entry = queue.get(attr) or {}
    for want_from, want_to in entry.get("candidates") or []:
        if (want_from, want_to) == (frm, to):
            found = {"title": f"{attr}: {frm} -> {to}"}
            if entry.get("by"):
                found["by"] = entry["by"]
            return found
    return None


def merge_bot(pr, only_by_name, packages, index):
    """Whether a maintainer can merge it with the merge bot (nixpkgs'
    ci/README.md), as far as the digest can tell: by-name only, into a
    development branch, opened by r-ryantm (a committer's approval also
    counts, but who's a committer isn't known here: approvedBy says who
    approved), no changes requested; and who could (the maintainers of
    every package it touches). {"ready": CI green, "maintainers": [...]}
    or None."""
    if not only_by_name or pr["base"] not in DEVELOPMENT or pr["draft"]:
        return None
    if pr["author"] != BOT or pr["review"] == "CHANGES_REQUESTED":
        return None
    each = [set((index.get(p) or {}).get("maintainers") or []) for p in packages]
    maintainers = sorted(set.intersection(*each)) if each else []
    if not maintainers:
        return None
    return {"ready": pr["ci"] == "SUCCESS", "maintainers": maintainers}


def analyse(prs, index, master, queue, hashes):
    """Each PR with what it is (buckets, title parts, packages,
    maintainers, update state, bot blocking, merge bot), and the groups of
    duplicates: the same diff (hashes: {number: fingerprint}), or several
    open PRs for the same attribute. Returns the PRs and the groups."""
    by_hash, by_attr = {}, {}
    for pr in prs:
        kind, attr, frm, to = title_parts(pr["title"])
        packages, only = by_name_packages(pr["files"])
        target = canonical(attr) if attr else None
        facts = {
            "buckets": buckets(pr, kind, packages, only),
            "packages": packages,
            "maintainers": sorted(
                {
                    m
                    for p in packages
                    for m in (index.get(p) or {}).get("maintainers", [])
                }
            ),
        }
        if kind == "update":
            now = master.get(target) or (index.get(target) or {}).get("version")
            facts["update"] = {"attr": attr, "from": frm, "to": to, "now": now}
            if state := update_state(frm, to, now):
                facts["state"] = state
            if blocking := blocks_bot(pr, target, frm, to, queue):
                facts["blocksBot"] = blocking
        if mb := merge_bot(pr, only, packages, index):
            facts["mergeBot"] = mb
        pr.update(facts)
        if h := hashes.get(pr["n"]):
            pr["diff"] = h
            by_hash.setdefault(h, []).append(pr["n"])
        if attr and kind in ("update", "init"):
            by_attr.setdefault(target, []).append(pr["n"])
    groups = []
    for kind, found in (("sameDiff", by_hash), ("samePackage", by_attr)):
        for key, numbers in sorted(found.items()):
            if len(numbers) > 1:
                groups.append({"kind": kind, "key": key, "prs": sorted(numbers)})
    return prs, groups
