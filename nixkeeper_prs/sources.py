"""What the digest knows about nixpkgs' packages, from where nixkeeper reads
it: the nixos-unstable channel's package index (each attribute's version,
maintainers and source file), master's versions from nixkeeper-hydra's
digest (what Hydra built last), and the update bot's queue from
nixkeeper-updates' digest (the versions it would update to next)."""

import csv
import gzip
import io
import json
import os
import re
from datetime import datetime, timedelta

from . import fetch

CHANNEL_INDEX_URL = os.environ.get(
    "NIXKEEPER_PRS_CHANNEL_INDEX",
    "https://channels.nixos.org/nixos-unstable/packages.json.br",
)
HYDRA_DIGEST_URL = os.environ.get(
    "NIXKEEPER_PRS_HYDRA_DIGEST",
    "https://raw.githubusercontent.com/iedame/nixkeeper-hydra/data/data/builds.csv.gz",
)
CHANNEL_REVISION_URL = os.environ.get(
    "NIXKEEPER_PRS_CHANNEL_REVISION",
    "https://channels.nixos.org/nixos-unstable/git-revision",
)
QUEUE_URL = os.environ.get(
    "NIXKEEPER_PRS_QUEUE",
    "https://raw.githubusercontent.com/iedame/nixkeeper-updates/data/data/queue.json.gz",
)
# Titles name Python packages by their alias (python3Packages.foo), the index
# by the versioned set it points to.
ALIASES = ((re.compile(r"^python3Packages\."), "python313Packages."),)
# A package directory: pkgs/by-name/<shard>/<name>/...
BY_NAME = re.compile(r"^pkgs/by-name/[^/]+/([^/]+)/")


def canonical(attr):
    for pattern, alias in ALIASES:
        attr = pattern.sub(alias, attr)
    return attr


def channel(url=CHANNEL_INDEX_URL):
    """{attribute: {"pname", "version", "maintainers": [GitHub handles]}}
    of the channel's package index."""
    import brotli  # the flake's Python has it (nixkeeper's sync needs it too)

    packages = json.loads(brotli.decompress(fetch.get(url)))["packages"]
    found = {}
    for attr, p in packages.items():
        meta = p.get("meta") or {}
        handles = [
            m["github"]
            for m in meta.get("maintainers") or []
            if isinstance(m, dict) and isinstance(m.get("github"), str)
        ]
        found[attr] = {
            "pname": p.get("pname") or "",
            "version": p.get("version") or "",
            "maintainers": handles,
        }
    return found


def hydra(url=HYDRA_DIGEST_URL):
    """nixkeeper-hydra's digest: its rows (every job of master's newest
    evaluation)."""
    text = gzip.decompress(fetch.get(url)).decode()
    return list(csv.DictReader(io.StringIO(text)))


def master(index, rows):
    """{attribute: master's version} from what Hydra built last on
    x86_64-linux (rows: hydra's): its build's name less the package's name
    (pname, from the channel's index: "wesnoth-1.18.9" → "1.18.9")."""
    found = {}
    for row in rows:
        if row["system"] != "x86_64-linux" or not row["name"]:
            continue
        pname = (index.get(row["attr"]) or {}).get("pname")
        if pname and row["name"].startswith(pname + "-"):
            found[row["attr"]] = row["name"][len(pname) + 1 :]
    return found


def jobs(rows):
    """{attribute in lower case: {system: {"status", "build", "reason"?}}}
    of Hydra's jobs (rows: hydra's); reason: why a failed build failed
    (nixkeeper-hydra's, from its log)."""
    found = {}
    for row in rows:
        job = {"status": row["status"], "build": row["build"]}
        if row.get("failedBecause"):
            job["reason"] = row["failedBecause"]
        found.setdefault(row["attr"].lower(), {})[row["system"]] = job
    return found


def queue(url=QUEUE_URL):
    """{attribute: {"candidates": [[from, to], ...], "by": day}}: what the
    update bot would update each package to next, and the day it's expected
    to try (nixkeeper-updates' queue: a package at position p is tried about
    p / positions × cycleDays after the queue was made)."""
    data = json.loads(gzip.decompress(fetch.get(url)))
    made = data.get("updatedAt")
    cycle, positions = data.get("cycleDays"), data.get("positions")
    found = {}
    for attr, entry in (data.get("queue") or {}).items():
        item = {
            "candidates": [
                [c[0], c[1]] for c in entry.get("candidates") or [] if len(c) >= 2
            ]
        }
        if made and cycle and positions and entry.get("position") is not None:
            days = entry["position"] / positions * cycle
            when = datetime.fromisoformat(made) + timedelta(days=days)
            item["by"] = when.date().isoformat()
        found[attr] = item
    return found


def channel_revision(url=CHANNEL_REVISION_URL):
    """The nixpkgs commit the nixos-unstable channel is at."""
    return fetch.get(url).decode().strip()
