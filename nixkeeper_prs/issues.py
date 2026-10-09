"""Build-failure issues against what Hydra builds now (nixkeeper-hydra's
digest of master): does the package an issue says fails build there, on
the platforms it names? An issue whose package builds on every such job is
a candidate to close, unless its title adds a condition (an option, an
override, a setup Hydra doesn't build: "with ROCm", "in a non-default
store"), which only a person can check.

Read from titles ("Build failure: foo on Darwin", nixpkgs' template), so a
guess: each issue says what it checked."""

import re

BUILD_FAILURE = re.compile(r"^\s*\[?build failure\]?\s*:?", re.IGNORECASE)
PACKAGE = re.compile(r"^[\s`'\"]*([A-Za-z_][\w.+-]*)")
# Platforms a title can name, as Hydra's systems.
PLATFORMS = (
    ("aarch64-darwin", r"aarch64-darwin|apple silicon|\bm[1-4]\b"),
    ("x86_64-darwin", r"x86_64-darwin|intel mac"),
    ("darwin", r"\bdarwin\b|\bmac ?os\b|\bmacos\b"),
    ("aarch64-linux", r"aarch64-linux|\baarch64\b|\barm64\b"),
    ("x86_64-linux", r"x86_64-linux"),
)
# Words that only say where (removed before looking for a condition).
WHERE = re.compile(
    r"\b(?:on|for)\s+(?:nixos\s+)?(?:" + "|".join(rx for _, rx in PLATFORMS) + r")"
    r"|" + "|".join(rx for _, rx in PLATFORMS),
    re.IGNORECASE,
)
# Builds Hydra doesn't make: other libcs, static, cross, other platforms.
VARIANT = re.compile(
    r"musl|static|cross|pkgscross|\bi686\b|riscv|armv[67]|powerpc|ppc64|freebsd"
    r"|windows|mingw|\bwasm",
    re.IGNORECASE,
)
# Hydra's statuses that say the package itself didn't build.
FAILING = {"failed"}
WAITING = {"dependency", "unfinished", "queued"}
PYTHON_ALIAS = re.compile(r"^python3Packages\.", re.IGNORECASE)


def parse(title):
    """(package, platforms named, condition: the title says more than the
    package and where) of a build-failure title; (None, ...) for any other."""
    m = BUILD_FAILURE.match(title)
    if not m:
        return None, [], False
    rest = title[m.end() :]
    p = PACKAGE.match(rest)
    if not p:
        return None, [], False
    package = p.group(1).rstrip(".,:")
    after = rest[p.end() :]
    # Most specific first, each word counted once (aarch64-darwin isn't also
    # darwin and aarch64).
    platforms, text = [], title
    for system, rx in PLATFORMS:
        if re.search(rx, text, re.IGNORECASE):
            platforms.append(system)
            text = re.sub(rx, " ", text, flags=re.IGNORECASE)
    leftover = re.sub(r"[\s\W_]+", " ", WHERE.sub(" ", after)).strip()
    return package, platforms, bool(leftover)


def jobs_for(package, jobs):
    """The package's Hydra jobs ({system: {"status", "reason"?, "build"}}),
    by its attribute (any case, python3Packages by its versioned set)."""
    key = package.lower()
    return jobs.get(key) or jobs.get(PYTHON_ALIAS.sub("python313packages.", key)) or {}


def check(title, jobs, python_set="python313packages."):
    """What Hydra says of a build-failure issue: None for other titles, else
    {"package", "verdict", "systems"?, "condition"?, "reasons"?}. verdict:
    "builds" (every job it concerns is ok), "failing" (one failed: its
    reasons by system), "waiting" (a dependency failed, or not finished),
    "variant" (a build Hydra doesn't make: musl, static, cross...), "noJob"
    (no Hydra job by that name), "platformNotBuilt" (only platforms Hydra
    doesn't build, x86_64-darwin)."""
    package, platforms, condition = parse(title)
    if not package:
        return None
    found = {"package": package}
    if condition:
        found["condition"] = True
    if VARIANT.search(title):
        found["verdict"] = "variant"
        return found
    mine = jobs_for(package, jobs)
    if not mine:
        found["verdict"] = "noJob"
        return found
    wanted = [
        system
        for system in sorted(mine)
        if not platforms
        or any(
            system == p or (p == "darwin" and system.endswith("-darwin"))
            for p in platforms
        )
    ]
    if not wanted:
        found["verdict"] = "platformNotBuilt"
        return found
    found["systems"] = wanted
    statuses = {system: mine[system]["status"] for system in wanted}
    if any(s in FAILING for s in statuses.values()):
        found["verdict"] = "failing"
        found["reasons"] = {
            system: mine[system].get("reason") or ""
            for system, s in statuses.items()
            if s in FAILING
        }
    elif all(s == "ok" for s in statuses.values()):
        found["verdict"] = "builds"
    else:
        found["verdict"] = "waiting"
    return found
