"""What each open PR is and where it stands, from its details and what
nixpkgs has: buckets (what kind of change), an update's state against the
channel and master (superseded, overtaken, backwards), whether it blocks
the update bot, whether a maintainer can merge it through the merge bot,
and the PRs it duplicates.

Versions are ordered as nixkeeper orders them (its versions.py: Repology's
libversion, with nixpkgs' "unstable")."""

import re

from nixkeeper.versions import is_newer

from . import nixversions
from .sources import BY_NAME, canonical

BOT = "r-ryantm"
# nixpkgs' CI opens PRs of its own (automated, like the update bot's).
CI_BOT = "nixpkgs-ci"
# nixpkgs' CI labels a PR the merge bot would merge for a maintainer (it
# knows who's a committer: the digest doesn't).
MERGE_BOT_LABEL = "2.status: merge-bot eligible"
# nixpkgs' title conventions (CONTRIBUTING.md): "attr: 1.0 -> 1.1",
# "attr: init at 1.0", "attr: drop", "attr: remove".
UPDATE = re.compile(r"^([\w.+-]+): (\S+) (?:->|→) (\S+)\s*$")
INIT = re.compile(r"^([\w.+-]+): init at \S+", re.IGNORECASE)
DROP = re.compile(r"^([\w.+-]+): (?:drop|remove)\b", re.IGNORECASE)
# A version has a digit somewhere: "ci: npins → flake" is no update.
DIGIT = re.compile(r"\d")
# A snapshot's version (nixpkgs' "0.1.0-unstable-2024-09-01", or a date).
SNAPSHOT = re.compile(r"unstable|\d{4}-\d{2}-\d{2}")
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
    m = UPDATE.match(title)
    if m and DIGIT.search(m.group(2)) and DIGIT.search(m.group(3)):
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
    if pr["author"] == CI_BOT:
        found.append("ci-bot")
    for name, pattern in ECOSYSTEMS:
        if any(pattern.search(f) for f in pr["files"]):
            found.append(name)
    if pr["changedFiles"] >= 50:
        found.append("treewide")
    return found


def update_state(frm, to, now):
    """Where an update ("frm -> to") stands. Backwards by Nix's own order
    (nixversions: what nixpkgs calls newer): "snapshotToRelease" (from a
    snapshot, 0.1.0-unstable-2024-09-01, to a release: usually a deliberate
    switch back to a tagged release) or "downgrade". "preRelease": newer for
    Nix, older for libversion, which reads the suffix as a pre-release
    (1.1.0 -> 1.1.0.dev0, 3.1 -> 3.1_p1): worth a look. Against what nixpkgs
    has now (now: master's version, else the channel's), by nixkeeper's
    order: "superseded" (nixpkgs moved past frm and has to, or newer: close
    it), "overtaken" (moved past frm, not up to to: rebase). Else None."""
    if nixversions.compare(to, frm) < 0:
        if SNAPSHOT.search(frm) and not SNAPSHOT.search(to):
            return "snapshotToRelease"
        return "downgrade"
    if is_newer(frm, to):
        return "preRelease"
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
    ci/README.md), and who could (the maintainers of every package it
    touches). nixpkgs' CI says so with its label (MERGE_BOT_LABEL: it also
    counts committers' PRs and approvals); without it, as far as the
    digest can tell: by-name only, into a development branch, opened by
    r-ryantm, no changes requested. {"ready": CI green, "maintainers":
    [...], "label": True when the label says so} or None."""
    labelled = MERGE_BOT_LABEL in pr["labels"]
    if pr["draft"] or (not labelled and not only_by_name):
        return None
    if not labelled and (
        pr["base"] not in DEVELOPMENT
        or pr["author"] != BOT
        or pr["review"] == "CHANGES_REQUESTED"
    ):
        return None
    each = [set((index.get(p) or {}).get("maintainers") or []) for p in packages]
    maintainers = sorted(set.intersection(*each)) if each else []
    if not maintainers and not labelled:
        return None
    found = {"ready": pr["ci"] == "SUCCESS", "maintainers": maintainers}
    if labelled:
        found["label"] = True
    return found


def hydra_failing(names, jobs):
    """{package: {system: reason}} of the packages named (a PR's) whose
    build fails on Hydra now (jobs: sources.jobs'; reason "" when
    nixkeeper-hydra hasn't one): a PR touching one may be its fix."""
    found = {}
    for name in sorted(names):
        failed = {
            system: job.get("reason") or ""
            for system, job in (jobs.get(name.lower()) or {}).items()
            if job["status"] == "failed"
        }
        if failed:
            found[name] = failed
    return found


def already_in(attr, index, master):
    """{"attr", "version"} when an init PR's attribute is in nixpkgs already
    (the channel's index, or master: built by Hydra): added some other way
    while the PR waited. None else."""
    version = master.get(attr) or (index.get(attr) or {}).get("version")
    if attr in master or attr in index:
        return {"attr": attr, "version": version or ""}
    return None


def analyse(prs, index, master, queue, known, jobs=None):
    """Each PR with what it is (buckets, title parts, packages,
    maintainers, update state, bot blocking, merge bot, an init's package
    already in nixpkgs, its packages failing on Hydra), and the groups of
    duplicates: the same diff (known: {number: (fingerprint, diff facts)},
    diffs.facts'), the same change (only its changed lines), or several
    open PRs for the same attribute. Returns the PRs and the groups."""
    by_hash, by_change, by_attr = {}, {}, {}
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
        if kind == "init" and (there := already_in(target, index, master)):
            facts["alreadyIn"] = there
        touched = set(packages) | ({target} if target and kind != "init" else set())
        if failing := hydra_failing(touched, jobs or {}):
            facts["hydraFailing"] = failing
        pr.update(facts)
        if pr["n"] in known:
            h, seen = known[pr["n"]]
            pr["diff"] = h
            by_hash.setdefault(h, []).append(pr["n"])
            if seen:
                pr["diffFacts"] = seen
                by_change.setdefault(seen["change"], []).append(pr["n"])
                if seen.get("versionOnly"):
                    pr["buckets"].append("version-only")
                if seen.get("cves"):
                    pr["buckets"].append("cve")
        if attr and kind in ("update", "init"):
            by_attr.setdefault(target, []).append(pr["n"])
    groups = []
    exact = set()
    for key, numbers in sorted(by_hash.items()):
        if len(numbers) > 1:
            groups.append({"kind": "sameDiff", "key": key, "prs": sorted(numbers)})
            exact.add(tuple(sorted(numbers)))
    for key, numbers in sorted(by_change.items()):
        # Not when it's the very same group as a same-diff one.
        if len(numbers) > 1 and tuple(sorted(numbers)) not in exact:
            groups.append({"kind": "sameChange", "key": key, "prs": sorted(numbers)})
    for key, numbers in sorted(by_attr.items()):
        if len(numbers) > 1:
            groups.append({"kind": "samePackage", "key": key, "prs": sorted(numbers)})
    return prs, groups
