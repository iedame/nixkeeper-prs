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
from datetime import timedelta

from . import fetch
from .sources import BY_NAME

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
ISSUES_QUERY = """
query($cursor: String, $page: Int!) {
  repository(owner: "NixOS", name: "nixpkgs") {
    issues(states: OPEN, first: $page, after: $cursor,
           orderBy: {field: CREATED_AT, direction: ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { number title }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


def open_issues(tok):
    """Every open issue of nixpkgs: [{"n", "title"}] (nixkeeper counts a
    package's issues by the words in their titles)."""
    found, cursor = [], None
    clock = Clock("open issues", 25)
    while True:
        data = fetch.graphql(ISSUES_QUERY, {"cursor": cursor, "page": LIST_PAGE}, tok)
        issues = data["repository"]["issues"]
        found += [{"n": i["number"], "title": i["title"]} for i in issues["nodes"] if i]
        clock.tick(len(found), issues["totalCount"], data.get("rateLimit"))
        if not issues["pageInfo"]["hasNextPage"]:
            return found
        cursor = issues["pageInfo"]["endCursor"]


MERGED_QUERY = """
query($q: String!, $after: String) {
  search(type: ISSUE, query: $q, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title isDraft baseRefName mergedAt
        author { login } mergedBy { login }
        files(first: 100) { nodes { path } }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""
COMMIT_DATE_QUERY = """
query($rev: String!) {
  repository(owner: "NixOS", name: "nixpkgs") {
    object(expression: $rev) { ... on Commit { committedDate } }
  }
}
"""
# GitHub's search gives at most 1,000 results: merged PRs are listed in
# windows of this many hours (nixpkgs merges a few hundred a day into master),
# as nixkeeper's own listing does.
WINDOW_HOURS = 12


def commit_date(revision, tok):
    """When a nixpkgs commit was committed (ISO 8601), or None."""
    data = fetch.graphql(COMMIT_DATE_QUERY, {"rev": revision}, tok)
    return ((data.get("repository") or {}).get("object") or {}).get("committedDate")


def merged_since(since, now, tok):
    """PRs merged into master from since to now (datetimes): [{"n", "title",
    "draft", "base", "merged", "author", "mergedBy", "packages"}] (author
    and mergedBy: GitHub logins, "ghost" for a deleted account; packages:
    the pkgs/by-name ones its files touch, of its first 100 files: who to
    credit for an update, and likely for a build fix, in nixkeeper's
    fixes). Raises when a window has more than search's 1,000 results
    (some would be missing)."""
    found, start = [], since
    while start < now:
        stop = min(start + timedelta(hours=WINDOW_HOURS), now)
        window = f"{start:%Y-%m-%dT%H:%M:%SZ}..{stop:%Y-%m-%dT%H:%M:%SZ}"
        query = f"repo:NixOS/nixpkgs is:pr is:merged base:master merged:{window}"
        after = None
        while True:
            data = fetch.graphql(MERGED_QUERY, {"q": query, "after": after}, tok)
            page = data["search"]
            if page["issueCount"] > 1000:
                raise OSError(f"more than 1,000 PRs merged in {window}")
            found += [
                {
                    "n": p["number"],
                    "title": p["title"],
                    "draft": p["isDraft"],
                    "base": p["baseRefName"],
                    "merged": p["mergedAt"],
                    "author": (p.get("author") or {}).get("login") or "ghost",
                    "mergedBy": (p.get("mergedBy") or {}).get("login") or "ghost",
                    "packages": sorted(
                        {
                            m.group(1)
                            for f in (p.get("files") or {}).get("nodes") or []
                            if f and (m := BY_NAME.match(f.get("path") or ""))
                        }
                    ),
                }
                for p in page["nodes"]
                if p and p.get("number")
            ]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        start = stop
    return found


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
