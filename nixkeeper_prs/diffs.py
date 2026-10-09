"""What a PR's diff says, read once per head commit (the diff itself isn't
kept): its fingerprints, and facts a reviewer would look for first.

    change       a fingerprint of the changed lines only (+/-, no context,
                 no line numbers): the same edit on another base or with
                 other lines around it
    versionOnly  every changed line is a version, hash or revision: a plain
                 version bump, the safest review there is
    tags         migrations it makes (nixpkgs' tracking issues): strictDeps,
                 structuredAttrs, rev → tag, SRI hash, drop with lib,
                 pyproject, finalAttrs, updateScript, pythonImportsCheck,
                 by-name
    cves         CVE ids in added lines (a patch named after one: a security
                 fix, labelled or not)
    hints        in the files it adds (a new package): what a reviewer would
                 ask (rec, with lib, rev instead of tag, an old-style hash,
                 Python without pythonImportsCheck); only what the PR adds,
                 not what it didn't touch

Read from the diff's text, so a guess at what the Nix means, not an
evaluation; each fact says what it saw."""

import hashlib
import re

CVE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
# A changed line that only moves a version (a plain bump).
VERSION_LINE = re.compile(
    r"""^\s*(?:version|rev|tag|hash|sha256|sha512|outputHash|vendorHash
    |cargoHash|cargoSha256|npmDepsHash|pnpmDepsHash|yarnHash|mvnHash
    |srcHash|depsHash|goModHash|commit|date)\s*=\s*"[^"]*"\s*;\s*(?:\#.*)?$""",
    re.VERBOSE,
)
# Migrations, by what a PR adds and removes.
ADDS = {
    "strictDeps": re.compile(r"\bstrictDeps\s*=\s*true\b"),
    "structuredAttrs": re.compile(r"\b__structuredAttrs\s*=\s*true\b"),
    "pyproject": re.compile(r"\bpyproject\s*=\s*true\b"),
    "finalAttrs": re.compile(r"\(\s*finalAttrs\s*:"),
    "updateScript": re.compile(r"\bupdateScript\s*="),
    "pythonImportsCheck": re.compile(r"\bpythonImportsCheck\s*="),
}
REV = re.compile(r"^\s*rev\s*=")
TAG = re.compile(r"^\s*tag\s*=")
OLD_HASH = re.compile(r"^\s*sha256\s*=\s*\"")
SRI = re.compile(r"^\s*hash\s*=\s*\"sha(?:256|512)-")
WITH_LIB = re.compile(r"\bwith lib(?:\.\w+)*;")
REC = re.compile(r"\bmkDerivation\s+rec\b|\bbuildPythonPackage\s+rec\b|=\s*rec\s*\{")
PYTHON = re.compile(r"\bbuildPython(?:Package|Application)\b")


def parse(diff):
    """[{"path", "new": added file, "renamedTo": path or None, "added":
    [lines], "removed": [lines]}] of a unified diff (bytes)."""
    files, cur = [], None
    for raw in diff.decode("utf-8", "replace").splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(\S+) b/(\S+)", raw)
            cur = {
                "path": m.group(2) if m else raw,
                "new": False,
                "renamedTo": None,
                "added": [],
                "removed": [],
            }
            files.append(cur)
        elif cur is None:
            continue
        elif raw.startswith("new file mode"):
            cur["new"] = True
        elif raw.startswith("rename to "):
            cur["renamedTo"] = raw[len("rename to ") :]
        elif raw.startswith(("+++", "---", "index ", "@@", "similarity ")):
            continue
        elif raw.startswith("+"):
            cur["added"].append(raw[1:])
        elif raw.startswith("-"):
            cur["removed"].append(raw[1:])
    return files


def change_hash(files):
    """The changed lines' fingerprint: paths and +/- lines only."""
    h = hashlib.sha256()
    for f in files:
        h.update(f["path"].encode() + b"\0")
        for line in f["removed"]:
            h.update(b"-" + line.encode() + b"\n")
        for line in f["added"]:
            h.update(b"+" + line.encode() + b"\n")
    return h.hexdigest()[:16]


def version_only(files):
    """Every changed line (not blank) moves a version, hash or revision, in
    files that already existed."""
    changed = [
        line for f in files for line in f["added"] + f["removed"] if line.strip()
    ]
    return (
        bool(changed)
        and not any(f["new"] or f["renamedTo"] for f in files)
        and all(VERSION_LINE.match(line) for line in changed)
    )


def tags(files):
    """The migrations it makes, in files that already existed (a new file
    declares what it declares: a new package's are hints' business)."""
    changed = [f for f in files if not f["new"]]
    added = [line for f in changed for line in f["added"]]
    removed = [line for f in changed for line in f["removed"]]
    found = {name for name, pattern in ADDS.items() if any(map(pattern.search, added))}
    if any(map(REV.match, removed)) and any(map(TAG.match, added)):
        found.add("revToTag")
    if any(map(OLD_HASH.match, removed)) and any(map(SRI.match, added)):
        found.add("sriHash")
    if any(map(WITH_LIB.search, removed)) and not any(map(WITH_LIB.search, added)):
        found.add("dropWithLib")
    if any(
        (f["renamedTo"] or "").startswith("pkgs/by-name/")
        and not f["path"].startswith("pkgs/by-name/")
        for f in files
    ):
        found.add("byName")
    return sorted(found)


def hints(files):
    """For the Nix files it adds: what a reviewer would ask."""
    found = set()
    for f in files:
        if not (f["new"] and f["path"].endswith(".nix")):
            continue
        text = f["added"]
        if any(map(REC.search, text)):
            found.add("rec")
        if any(map(WITH_LIB.search, text)):
            found.add("withLib")
        if any(map(REV.match, text)) and not any(map(TAG.match, text)):
            found.add("revNotTag")
        if any(map(OLD_HASH.match, text)):
            found.add("oldHash")
        if any(map(PYTHON.search, text)) and not any(
            ADDS["pythonImportsCheck"].search(line) for line in text
        ):
            found.add("noPythonImportsCheck")
    return sorted(found)


def facts(diff):
    """What a diff (bytes) says: {"change", "versionOnly", "tags", "cves",
    "hints"}, the empty ones left out."""
    files = parse(diff)
    found = {"change": change_hash(files)}
    if version_only(files):
        found["versionOnly"] = True
    for key, value in (
        ("tags", tags(files)),
        (
            "cves",
            sorted(
                {
                    c.upper()
                    for f in files
                    for line in f["added"]
                    for c in CVE.findall(line)
                }
            ),
        ),
        ("hints", hints(files)),
    ):
        if value:
            found[key] = value
    return found
