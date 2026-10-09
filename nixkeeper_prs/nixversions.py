"""Nix's own version order: `builtins.compareVersions`, as nix and nixpkgs'
tools (nixpkgs-update: "not newer according to Nix") compare versions. A
port of nix's compareVersions (src/libstore/names.cc): a version splits
into components at dots and dashes, each a run of digits or of other
characters; components compare as numbers when both are, "" (one version
ran out) is below a number, "pre" is below anything else, a number is
above any word ("2.3a" < "2.3.1"), and words compare as strings.

nixkeeper orders versions as Repology does (libversion), which reads
suffixes as pre-releases where Nix doesn't ("1.1.0.dev0" is newer than
"1.1.0" for Nix, older for libversion): for what nixpkgs calls newer, this
one decides."""


def _digit(char):
    """C's isdigit: ASCII digits only (Python's isdigit takes "²" too)."""
    return "0" <= char <= "9"


def _components(version):
    """Its components, as nix's nextComponent splits them."""
    found, i, n = [], 0, len(version)
    while i < n:
        while i < n and version[i] in ".-":
            i += 1
        if i >= n:
            break
        start = i
        if _digit(version[i]):
            while i < n and _digit(version[i]):
                i += 1
        else:
            while i < n and not _digit(version[i]) and version[i] not in ".-":
                i += 1
        found.append(version[start:i])
    return found


# nix reads a component as a C int (string2Int<int>): longer digit runs (a
# date with the time, 20260901092954; a commit hash's digits) don't fit and
# compare as words.
INT_MAX = 2**31 - 1


def _number(component):
    if not component or not all(map(_digit, component)):
        return None
    value = int(component)
    return value if value <= INT_MAX else None


def _less(c1, c2):
    """nix's componentsLT."""
    n1, n2 = _number(c1), _number(c2)
    if n1 is not None and n2 is not None:
        return n1 < n2
    if c1 == "" and n2 is not None:
        return True
    if c1 == "pre" and c2 != "pre":
        return True
    if c2 == "pre":
        return False
    if n2 is not None:
        return True
    if n1 is not None:
        return False
    return c1 < c2


def compare(v1, v2):
    """-1, 0 or 1, as builtins.compareVersions v1 v2."""
    a, b = _components(v1), _components(v2)
    for i in range(max(len(a), len(b))):
        c1 = a[i] if i < len(a) else ""
        c2 = b[i] if i < len(b) else ""
        if _less(c1, c2):
            return -1
        if _less(c2, c1):
            return 1
    return 0
