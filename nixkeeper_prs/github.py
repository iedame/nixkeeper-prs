"""nixpkgs' open pull requests, from GitHub: every one's details through the
GraphQL API (PAGE a request, oldest first), and a PR's diff from github.com.

GraphQL counts a query's cost by its connections, not its nodes: PAGE PRs
with their files, labels, reviews and last commit cost a few points, ~500
pages about a thousand, within the workflow token's hourly limit."""

import hashlib
import re
import sys

from . import fetch

PAGE = 25
QUERY = """
query($cursor: String, $page: Int!) {
  repository(owner: "NixOS", name: "nixpkgs") {
    pullRequests(states: OPEN, first: $page, after: $cursor,
                 orderBy: {field: CREATED_AT, direction: ASC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
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
      }
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


def open_prs(tok):
    """Every open PR of nixpkgs (pr's), oldest first, and how many GitHub
    says are open."""
    found, cursor, total = [], None, None
    while True:
        data = fetch.graphql(QUERY, {"cursor": cursor, "page": PAGE}, tok)
        prs = data["repository"]["pullRequests"]
        total = prs["totalCount"]
        found += [pr(node) for node in prs["nodes"]]
        if len(found) % 1000 < PAGE:
            rate = data.get("rateLimit") or {}
            print(
                f"  {len(found):,} of {total:,} PRs "
                f"(GraphQL points left: {rate.get('remaining')})",
                file=sys.stderr,
            )
        if not prs["pageInfo"]["hasNextPage"]:
            return found, total
        cursor = prs["pageInfo"]["endCursor"]


DIFF_URL = "https://github.com/NixOS/nixpkgs/pull/{n}.diff"
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


def diff(n):
    """PR n's diff (bytes), from github.com (not the API: no token, no
    limit of its own beyond fetch's pace)."""
    return fetch.get(DIFF_URL.format(n=n))
