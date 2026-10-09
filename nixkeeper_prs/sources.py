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

from . import fetch

CHANNEL_INDEX_URL = os.environ.get(
    "NIXKEEPER_PRS_CHANNEL_INDEX",
    "https://channels.nixos.org/nixos-unstable/packages.json.br",
)
HYDRA_DIGEST_URL = os.environ.get(
    "NIXKEEPER_PRS_HYDRA_DIGEST",
    "https://raw.githubusercontent.com/iedame/nixkeeper-hydra/data/data/builds.csv.gz",
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


def master(index, url=HYDRA_DIGEST_URL):
    """{attribute: master's version} from what Hydra built last on
    x86_64-linux: its build's name less the package's name (pname, from
    the channel's index: "wesnoth-1.18.9" → "1.18.9")."""
    text = gzip.decompress(fetch.get(url)).decode()
    found = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row["system"] != "x86_64-linux" or not row["name"]:
            continue
        pname = (index.get(row["attr"]) or {}).get("pname")
        if pname and row["name"].startswith(pname + "-"):
            found[row["attr"]] = row["name"][len(pname) + 1 :]
    return found


def queue(url=QUEUE_URL):
    """{attribute: [[from, to], ...]}: what the update bot would update each
    package to next (nixkeeper-updates' queue)."""
    data = json.loads(gzip.decompress(fetch.get(url)))
    return {
        attr: [[c[0], c[1]] for c in entry.get("candidates") or [] if len(c) >= 2]
        for attr, entry in (data.get("queue") or {}).items()
    }
