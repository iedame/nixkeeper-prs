"""What each PR is and where it stands (facts.py)."""

import unittest

from nixkeeper_prs import facts


def pr(n, title, author="someone", files=("pkgs/by-name/fo/foo/package.nix",), **more):
    return {
        "n": n,
        "title": title,
        "author": author,
        "draft": False,
        "created": "2026-10-01T00:00:00Z",
        "updated": "2026-10-02T00:00:00Z",
        "base": "master",
        "head": f"sha{n}",
        "additions": 2,
        "deletions": 2,
        "changedFiles": len(files),
        "files": list(files),
        "labels": [],
        "mergeable": "MERGEABLE",
        "review": None,
        "approvedBy": [],
        "ci": "SUCCESS",
        **more,
    }


INDEX = {
    "foo": {"pname": "foo", "version": "1.0", "maintainers": ["Alice", "bob"]},
    "bar": {"pname": "bar", "version": "2.0", "maintainers": ["bob"]},
    "python313Packages.baz": {"pname": "baz", "version": "0.5", "maintainers": []},
}


class Titles(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(
            facts.title_parts("foo: 1.0 -> 1.1"), ("update", "foo", "1.0", "1.1")
        )
        self.assertEqual(facts.title_parts("foo: init at 0.1")[:2], ("init", "foo"))
        self.assertEqual(facts.title_parts("foo: drop")[:2], ("drop", "foo"))
        self.assertEqual(facts.title_parts("treewide: lots")[0], None)


class UpdateState(unittest.TestCase):
    def test_states(self):
        self.assertIsNone(facts.update_state("1.0", "1.1", "1.0"))  # still the update
        self.assertEqual(facts.update_state("1.0", "1.1", "1.1"), "superseded")
        self.assertEqual(facts.update_state("1.0", "1.1", "1.2"), "superseded")
        self.assertEqual(facts.update_state("1.0", "1.5", "1.2"), "overtaken")
        # A snapshot to a release sorts backwards: a scheme change, not done.
        self.assertEqual(facts.update_state("1.0", "0.9", "1.0"), "backwards")
        self.assertIsNone(facts.update_state("1.0", "1.1", None))


class Bot(unittest.TestCase):
    QUEUE = {"foo": {"candidates": [["1.0", "1.1"]], "by": "2026-10-12"}}

    def test_a_persons_pr_with_the_bots_title(self):
        p = pr(1, "foo: 1.0 -> 1.1")
        self.assertEqual(
            facts.blocks_bot(p, "foo", "1.0", "1.1", self.QUEUE),
            {"title": "foo: 1.0 -> 1.1", "by": "2026-10-12"},
        )
        # Another version: the bot's search doesn't find it.
        self.assertIsNone(facts.blocks_bot(p, "foo", "1.0", "1.2", self.QUEUE))
        # The bot's own PR is its update, not a blocker.
        mine = pr(2, "foo: 1.0 -> 1.1", author="r-ryantm")
        self.assertIsNone(facts.blocks_bot(mine, "foo", "1.0", "1.1", self.QUEUE))


class MergeBot(unittest.TestCase):
    def test_the_bots_by_name_pr_and_who_can_merge(self):
        p = pr(1, "foo: 1.0 -> 1.1", author="r-ryantm")
        self.assertEqual(
            facts.merge_bot(p, True, ["foo"], INDEX),
            {"ready": True, "maintainers": ["Alice", "bob"]},
        )
        # Two packages: only who maintains both.
        self.assertEqual(
            facts.merge_bot(p, True, ["foo", "bar"], INDEX)["maintainers"], ["bob"]
        )

    def test_nixpkgs_ci_label_says_so(self):
        """A person's PR a committer approved: the digest can't tell, the
        label can."""
        p = pr(1, "foo: 1.0 -> 1.1", labels=[facts.MERGE_BOT_LABEL])
        self.assertEqual(
            facts.merge_bot(p, True, ["foo"], INDEX),
            {"ready": True, "maintainers": ["Alice", "bob"], "label": True},
        )

    def test_not_eligible(self):
        bot = pr(1, "foo: 1.0 -> 1.1", author="r-ryantm")
        self.assertIsNone(facts.merge_bot(bot, False, ["foo"], INDEX))  # not by-name
        person = pr(2, "foo: 1.0 -> 1.1")
        self.assertIsNone(facts.merge_bot(person, True, ["foo"], INDEX))
        asked = {**bot, "review": "CHANGES_REQUESTED"}
        self.assertIsNone(facts.merge_bot(asked, True, ["foo"], INDEX))
        staging = {**bot, "base": "release-26.05"}
        self.assertIsNone(facts.merge_bot(staging, True, ["foo"], INDEX))
        red = {**bot, "ci": "FAILURE"}
        self.assertFalse(facts.merge_bot(red, True, ["foo"], INDEX)["ready"])


class Analyse(unittest.TestCase):
    def test_facts_and_groups(self):
        prs = [
            pr(1, "foo: 1.0 -> 1.1"),
            pr(2, "foo: 1.0 -> 1.2"),
            pr(3, "bar: 1.5 -> 2.0"),  # nixpkgs has 2.0: superseded
            pr(4, "python3Packages.baz: 0.4 -> 0.5"),  # by its alias
            pr(5, "docs: typo", files=["doc/manual.md"]),
        ]
        hashes = {3: "aa", 5: "aa"}
        found, groups = facts.analyse(
            prs, INDEX, {}, {"foo": {"candidates": [["1.0", "1.1"]]}}, hashes
        )
        by_n = {p["n"]: p for p in found}
        self.assertEqual(by_n[1]["blocksBot"], {"title": "foo: 1.0 -> 1.1"})
        self.assertEqual(by_n[3]["state"], "superseded")
        self.assertEqual(by_n[4]["state"], "superseded")
        self.assertIn("by-name", by_n[1]["buckets"])
        self.assertIn("docs", by_n[5]["buckets"])
        self.assertEqual(by_n[1]["maintainers"], ["Alice", "bob"])
        self.assertEqual(
            groups,
            [
                {"kind": "sameDiff", "key": "aa", "prs": [3, 5]},
                {"kind": "samePackage", "key": "foo", "prs": [1, 2]},
            ],
        )

    def test_master_ahead_of_the_channel(self):
        found, _ = facts.analyse(
            [pr(1, "foo: 1.0 -> 1.1")], INDEX, {"foo": "1.1"}, {}, {}
        )
        self.assertEqual(found[0]["state"], "superseded")
        self.assertEqual(found[0]["update"]["now"], "1.1")


if __name__ == "__main__":
    unittest.main()
