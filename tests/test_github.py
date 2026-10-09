"""Reading nixpkgs' open PRs (github.py), the sources (sources.py), and a
run's diffs (cli.read_diffs)."""

import gzip
import json
import time
import unittest
from unittest import mock

from nixkeeper_prs import cli, fetch, github, sources

NODE = {
    "number": 7,
    "title": "foo: 1.0 -> 1.1",
    "isDraft": False,
    "createdAt": "2026-10-01T00:00:00Z",
    "updatedAt": "2026-10-02T00:00:00Z",
    "author": {"login": "r-ryantm"},
    "baseRefName": "master",
    "headRefOid": "abc",
    "additions": 2,
    "deletions": 2,
    "changedFiles": 1,
    "mergeable": "MERGEABLE",
    "reviewDecision": "APPROVED",
    "labels": {"nodes": [{"name": "10.rebuild-linux: 1-10"}]},
    "files": {"nodes": [{"path": "pkgs/by-name/fo/foo/package.nix"}]},
    "latestOpinionatedReviews": {
        "nodes": [
            {"state": "APPROVED", "author": {"login": "bob"}},
            {"state": "COMMENTED", "author": {"login": "carol"}},
        ]
    },
    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
}


def page(nodes, more, cursor="c"):
    return {
        "repository": {
            "pullRequests": {
                "totalCount": 2,
                "pageInfo": {"hasNextPage": more, "endCursor": cursor},
                "nodes": nodes,
            }
        },
        "rateLimit": {"remaining": 900},
    }


class GraphQL(unittest.TestCase):
    def test_a_pr(self):
        p = github.pr(NODE)
        self.assertEqual(
            (p["n"], p["author"], p["approvedBy"], p["ci"], p["files"]),
            (7, "r-ryantm", ["bob"], "SUCCESS", ["pkgs/by-name/fo/foo/package.nix"]),
        )

    def test_every_page(self):
        second = {**NODE, "number": 8, "author": None, "commits": {"nodes": []}}
        answers = [page([NODE], True), page([second], False)]
        with mock.patch.object(fetch, "graphql", side_effect=answers) as asked:
            prs, total = github.open_prs("token")
        self.assertEqual([p["n"] for p in prs], [7, 8])
        self.assertEqual((prs[1]["author"], prs[1]["ci"]), ("ghost", None))
        self.assertEqual(total, 2)
        self.assertEqual(asked.call_args_list[1].args[1]["cursor"], "c")


class Diffs(unittest.TestCase):
    A = (
        b"diff --git a/f b/f\nindex 1a2b..3c4d 100644\n--- a/f\n+++ b/f\n"
        b"@@ -10,3 +10,3 @@ foo\n-old\n+new\n"
    )

    def test_the_same_change_on_another_base(self):
        moved = self.A.replace(b"1a2b..3c4d", b"9e9e..8f8f").replace(
            b"-10,3 +10,3", b"-42,3 +42,3"
        )
        self.assertEqual(github.diff_hash(self.A), github.diff_hash(moved))
        other = self.A.replace(b"+new", b"+newer")
        self.assertNotEqual(github.diff_hash(self.A), github.diff_hash(other))

    def test_read_once_per_head_newest_first_small_only(self):
        prs = [
            {
                "n": 1,
                "head": "x",
                "updated": "2026-10-01",
                "changedFiles": 1,
                "additions": 1,
                "deletions": 1,
            },
            {
                "n": 2,
                "head": "y",
                "updated": "2026-10-03",
                "changedFiles": 1,
                "additions": 1,
                "deletions": 1,
            },
            {
                "n": 3,
                "head": "z",
                "updated": "2026-10-02",
                "changedFiles": 400,
                "additions": 9000,
                "deletions": 0,
            },
        ]
        cache = {"1": ["x", "aa"], "99": ["gone", "bb"]}
        with mock.patch.object(github, "diff", return_value=self.A) as got:
            read, pending = cli.read_diffs(prs, cache, time.monotonic())
        got.assert_called_once_with(2)  # 1 read at its head, 3 too big
        self.assertEqual((read, pending), (1, 0))
        self.assertNotIn("99", cache)  # no longer open
        self.assertEqual(cache["2"][0], "y")


class Sources(unittest.TestCase):
    def test_master_versions_by_pname(self):
        index = {"wesnoth": {"pname": "wesnoth", "version": "1.18.8"}}
        csv_text = (
            "attr,system,build,status,finished,name\n"
            "wesnoth,x86_64-linux,1,ok,,wesnoth-1.18.9\n"
            "wesnoth,aarch64-linux,2,ok,,wesnoth-1.18.10\n"
        )
        with mock.patch.object(
            fetch, "get", return_value=gzip.compress(csv_text.encode())
        ):
            self.assertEqual(sources.master(index), {"wesnoth": "1.18.9"})

    def test_queue(self):
        body = {"queue": {"unciv": {"candidates": [["4.22.5", "4.22.7", "url"]]}}}
        data = gzip.compress(json.dumps(body).encode())
        with mock.patch.object(fetch, "get", return_value=data):
            self.assertEqual(sources.queue(), {"unciv": [["4.22.5", "4.22.7"]]})

    def test_alias(self):
        self.assertEqual(
            sources.canonical("python3Packages.foo"), "python313Packages.foo"
        )


if __name__ == "__main__":
    unittest.main()


class Sweep(unittest.TestCase):
    NOW = cli.datetime.fromisoformat("2026-10-09T12:00:00+00:00")

    def old(self, n, updated, **more):
        return {**github.pr({**NODE, "number": n, "updatedAt": updated}), **more}

    def test_full_without_a_last_digest_or_when_a_day_old(self):
        with mock.patch.object(github, "open_prs", return_value=([], 0)) as full:
            self.assertEqual(cli.sweep(None, None, self.NOW, "t")[1], "full")
            day_old = "2026-10-08T11:00:00+00:00"
            self.assertEqual(
                cli.sweep([self.old(1, "u")], day_old, self.NOW, "t")[1], "full"
            )
        self.assertEqual(full.call_count, 2)

    def test_only_the_changed_read_again(self):
        previous = [
            self.old(1, "2026-10-08T00:00:00Z", buckets=["update"]),  # unchanged
            self.old(2, "2026-10-08T00:00:00Z"),  # updated since
            self.old(3, "2026-10-08T00:00:00Z", ci="PENDING"),  # unsettled
            self.old(4, "2026-10-08T00:00:00Z"),  # closed since
        ]
        listed = {
            1: "2026-10-08T00:00:00Z",
            2: "2026-10-09T00:00:00Z",
            3: "2026-10-08T00:00:00Z",
            5: "2026-10-09T00:00:00Z",  # new
        }
        fresh = [self.old(n, listed[n]) for n in (2, 3, 5)]
        with (
            mock.patch.object(github, "open_list", return_value=listed),
            mock.patch.object(github, "details", return_value=fresh) as read,
            mock.patch("sys.stderr"),
        ):
            prs, how, total = cli.sweep(
                previous, "2026-10-09T06:00:00+00:00", self.NOW, "t"
            )
        self.assertEqual(sorted(read.call_args.args[0]), [2, 3, 5])
        self.assertEqual((how, total), ("changed", 4))
        self.assertEqual([p["n"] for p in prs], [1, 2, 3, 5])
        # The last digest's worked-out facts aren't carried: analyse redoes them.
        self.assertNotIn("buckets", prs[0])

    def test_details_by_number(self):
        answer = {
            "repository": {
                "p7": {**NODE, "state": "OPEN"},
                "p8": {**NODE, "number": 8, "state": "CLOSED"},
                "p9": None,
            }
        }
        with mock.patch.object(fetch, "graphql", return_value=answer) as asked:
            found = github.details([9, 7, 8], "t")
        self.assertEqual([p["n"] for p in found], [7])
        query = asked.call_args.args[0]
        self.assertIn("p7: pullRequest(number: 7)", query)
        self.assertIn("p9: pullRequest(number: 9)", query)
