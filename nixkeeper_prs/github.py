"""nixpkgs' open pull requests, from GitHub: their details through the
GraphQL API, and a PR's diff from github.com.

Two ways to the details: a full sweep (every open PR with its details,
PAGE a request, oldest first: about an hour, as GitHub takes several
seconds a page), or a light list of every open PR's number and last update
(LIST_PAGE a request: minutes) and the details of the PRs that changed
since, by number (DETAILS_PAGE a request). GraphQL counts cost by
connections, not nodes: a full sweep costs ~460 points (2026-10-09)."""

import hashlib
import re
import sys
import time

from . import fetch

PAGE = 25
LIST_PAGE = 100
DETAILS_PAGE = 25
# A PR's details: the same fields in every query.
FIELDS = """
  number title isDraft createdAt updatedAt
  author { login }
  baseRefName headRefOid
  additions deletions changedFiles
  mergeable reviewDecision
  labels(first: 30) { nodes { name } }
  files(first: 100) { nodes { path additions deletions } }
  latestOpinionatedReviews(first: 20) {
    nodes { state author { login } }
  }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
"""
QUERY = """
query($cursor: String, $page: Int!) {
  repository(owner: "NixOS", name: "nixpkgs") {
    pullRequests(states: OPEN, first: $page, after: $cursor,
                 orderBy: {field: CREATED_AT, direction: ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { FIELDS }
    }
  }
  rateLimit { cost remaining resetAt }
}
""".replace("FIELDS", FIELDS)
LIST_QUERY = """
query($cursor: String, $page: Int!) {
  repository(owner: "NixOS", name: "nixpkgs") {
    pullRequests(states: OPEN, first: $page, after: $cursor,
                 orderBy: {field: CREATED_AT, direction: ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { number updatedAt }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


def pr(node):
    """A PR as the digest keeps it, from its GraphQL node."""
    commit = ((node.get("commits") or {}).get("nodes") or [{}])[0].get("commit") or {}
    reviews = (node.get("latestOpinionatedReviews") or {}).get("nodes") or []
    return {
        "n": node["number"],
        "title": node["title"],
        "author": (node.get("author") or {}).get("login") or "ghost",
        "draft": node["isDraft"],
        "created": node["createdAt"],
        "updated": node["updatedAt"],
        "base": node["baseRefName"],
        "head": node["headRefOid"],
        "additions": node["additions"],
        "deletions": node["deletions"],
        "changedFiles": node["changedFiles"],
        "files": [f["path"] for f in (node.get("files") or {}).get("nodes") or []],
        "labels": [x["name"] for x in (node.get("labels") or {}).get("nodes") or []],
        # CONFLICTING, MERGEABLE or UNKNOWN (GitHub hasn't worked it out yet).
        "mergeable": node.get("mergeable"),
        # APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED, or None.
        "review": node.get("reviewDecision"),
        "approvedBy": sorted(
            (r.get("author") or {}).get("login") or "ghost"
            for r in reviews
            if r["state"] == "APPROVED"
        ),
        # SUCCESS, FAILURE, ERROR, PENDING, EXPECTED, or None (no checks).
        "ci": (commit.get("statusCheckRollup") or {}).get("state"),
    }


class Clock:
    """How long GitHub takes to answer, logged every so many requests: the
    sweep's time is GitHub's, not the pace's."""

    def __init__(self, what, every):
        self.what, self.every, self.requests = what, every, 0
        self.started = time.monotonic()

    def tick(self, done, total, rate):
        self.requests += 1
        if self.requests % self.every == 0:
            took = time.monotonic() - self.started
            print(
                f"  {self.what}: {done:,} of {total:,} in {took / 60:.1f} min, "
                f"{took / self.requests:.1f} s a request "
                f"(GraphQL points left: {(rate or {}).get('remaining')})",
                file=sys.stderr,
            )


def _pages(query, page, tok, what, every):
    """Every node of a paged pullRequests query, and GitHub's totalCount."""
    found, cursor = [], None
    clock = Clock(what, every)
    while True:
        data = fetch.graphql(query, {"cursor": cursor, "page": page}, tok)
        prs = data["repository"]["pullRequests"]
        found += prs["nodes"]
        clock.tick(len(found), prs["totalCount"], data.get("rateLimit"))
        if not prs["pageInfo"]["hasNextPage"]:
            return found, prs["totalCount"]
        cursor = prs["pageInfo"]["endCursor"]


def open_prs(tok):
    """Every open PR of nixpkgs with its details (pr's), oldest first, and
    how many GitHub says are open: the full sweep."""
    nodes, total = _pages(QUERY, PAGE, tok, "full sweep", 40)
    return [pr(node) for node in nodes], total


def open_list(tok):
    """{number: last update} of every open PR: the light list."""
    nodes, _ = _pages(LIST_QUERY, LIST_PAGE, tok, "open PRs", 25)
    return {node["number"]: node["updatedAt"] for node in nodes}


def details(numbers, tok):
    """The details (pr's) of the PRs numbered, DETAILS_PAGE a request (one
    aliased field each); a PR closed meanwhile is left out."""
    found = []
    numbers = sorted(numbers)
    clock = Clock("changed PRs", 20)
    for start in range(0, len(numbers), DETAILS_PAGE):
        batch = numbers[start : start + DETAILS_PAGE]
        fields = "\n".join(
            f"p{n}: pullRequest(number: {n}) {{ state {FIELDS} }}" for n in batch
        )
        query = (
            'query { repository(owner: "NixOS", name: "nixpkgs") { '
            + fields
            + " } rateLimit { cost remaining resetAt } }"
        )
        data = fetch.graphql(query, {}, tok)
        for node in data["repository"].values():
            if node and node.get("state") == "OPEN":
                found.append(pr(node))
        clock.tick(start + len(batch), len(numbers), data.get("rateLimit"))
    return found


# A PR's diff through the REST API, with the token: github.com's
# pull/N.diff answers 429 to GitHub Actions' addresses after a few dozen
# (2026-10-09). One request each, within the token's 5,000 an hour.
DIFF_URL = "https://api.github.com/repos/NixOS/nixpkgs/pulls/{n}"
DIFF_TYPE = "application/vnd.github.diff"
# What changes between two copies of the same diff: the blob ids ("index
# 1a2b..3c4d 100644") and where the hunks fall ("@@ -12,7 +12,7 @@"), which
# move with the base the PR was made on.
INDEX = re.compile(rb"^index [0-9a-f]+\.\.[0-9a-f]+.*$", re.MULTILINE)
HUNK = re.compile(rb"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


def diff_hash(diff):
    """A diff's fingerprint: the same for the same change made on another
    base (blob ids and hunk positions left out)."""
    normal = HUNK.sub(b"@@", INDEX.sub(b"index", diff))
    return hashlib.sha256(normal).hexdigest()[:16]


def diff(n, tok):
    """PR n's diff (bytes), through GitHub's REST API."""
    return fetch.get(DIFF_URL.format(n=n), accept=DIFF_TYPE, tok=tok)
