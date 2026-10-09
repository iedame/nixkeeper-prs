"""Asking GitHub and the other digests, politely: one request at a time, at
most one a PAUSE, a User-Agent saying who asks, and a few tries on a server
error before giving up (the run then publishes nothing new)."""

import json
import os
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "nixkeeper-prs (+https://github.com/iedame/nixkeeper-prs)"
GRAPHQL_URL = "https://api.github.com/graphql"
# Seconds from one request to the next.
PAUSE = 1.0
TRIES = 3
# Server errors worth trying again after a wait (GitHub's GraphQL answers 502
# when a query takes too long).
RETRY = {500, 502, 503, 504}
_last = 0.0


def _pace():
    global _last
    wait = _last + PAUSE - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()


def _open(req, timeout=60):
    """The response's body, tried TRIES times on a server error or no
    answer; raises the last error."""
    for attempt in range(TRIES):
        _pace()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            e.close()
            if e.code not in RETRY or attempt == TRIES - 1:
                raise
            print(f"  {req.full_url}: {e.code}, trying again", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == TRIES - 1:
                raise
            print(f"  {req.full_url}: {e}, trying again", file=sys.stderr)
        time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def get(url, accept=None):
    """url's body (bytes)."""
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return _open(urllib.request.Request(url, headers=headers))


def token():
    """The token for GitHub's GraphQL API (it has no anonymous access): the
    workflow's own GITHUB_TOKEN, read-only on public data."""
    found = os.environ.get("GITHUB_TOKEN", "")
    if not found:
        raise OSError("GITHUB_TOKEN isn't set: GitHub's GraphQL API needs a token")
    return found


def graphql(query, variables, tok):
    """The data of a GraphQL query; raises on errors in the answer."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        },
    )
    answer = json.loads(_open(req, timeout=90))
    if answer.get("errors"):
        raise OSError(f"GraphQL: {answer['errors'][0].get('message')}")
    return answer["data"]
