"""Build-failure issues against Hydra's builds (issues.py)."""

import unittest

from nixkeeper_prs import issues

JOBS = {
    "logcheck": {
        "x86_64-linux": {"status": "ok", "build": "1"},
        "aarch64-darwin": {"status": "ok", "build": "2"},
    },
    "po4a": {
        "x86_64-linux": {"status": "failed", "build": "3", "reason": "tests"},
        "aarch64-darwin": {"status": "ok", "build": "4"},
    },
    "openroad": {"x86_64-linux": {"status": "failed", "build": "5", "reason": "link"}},
    "llama-cpp": {"x86_64-linux": {"status": "ok", "build": "6"}},
    "foo": {"x86_64-linux": {"status": "dependency", "build": "7"}},
    "python313packages.accelerate": {"x86_64-linux": {"status": "ok", "build": "8"}},
    "bar": {"x86_64-linux": {"status": "ok", "build": "9"}},
}


class Parse(unittest.TestCase):
    def test_package_platforms_condition(self):
        self.assertEqual(
            issues.parse("Build failure: logcheck"), ("logcheck", [], False)
        )
        self.assertEqual(
            issues.parse("Build failure: po4a on Darwin"), ("po4a", ["darwin"], False)
        )
        self.assertEqual(
            issues.parse("Build failure: llama-cpp (with opencl support)"),
            ("llama-cpp", [], True),
        )
        self.assertEqual(issues.parse("foo: crashes")[0], None)
        self.assertEqual(
            issues.parse("[Build failure] `qmk` on aarch64-darwin.")[:2],
            ("qmk", ["aarch64-darwin"]),
        )


class Check(unittest.TestCase):
    def verdict(self, title):
        return issues.check(title, JOBS)

    def test_builds_now(self):
        self.assertEqual(
            self.verdict("Build failure: logcheck"),
            {
                "package": "logcheck",
                "verdict": "builds",
                "systems": ["aarch64-darwin", "x86_64-linux"],
            },
        )

    def test_the_platform_named_only(self):
        # Fails on Linux, builds on Darwin: the issue is about Darwin.
        self.assertEqual(
            self.verdict("Build failure: po4a on Darwin")["verdict"], "builds"
        )
        failing = self.verdict("Build failure: po4a")
        self.assertEqual(
            (failing["verdict"], failing["reasons"]),
            ("failing", {"x86_64-linux": "tests"}),
        )

    def test_a_condition_is_said(self):
        found = self.verdict("Build failure: llama-cpp (with opencl support)")
        self.assertEqual((found["verdict"], found.get("condition")), ("builds", True))

    def test_others(self):
        self.assertEqual(self.verdict("Build failure: foo")["verdict"], "waiting")
        self.assertEqual(
            self.verdict("Build failure: pkgsMusl.bash")["verdict"], "variant"
        )
        self.assertEqual(
            self.verdict("Build failure: nosuchpackage")["verdict"], "noJob"
        )
        self.assertEqual(
            self.verdict("Build failure: bar on x86_64-darwin")["verdict"],
            "platformNotBuilt",
        )
        # By the alias.
        self.assertEqual(
            self.verdict("Build failure: python3Packages.accelerate")["verdict"],
            "builds",
        )
        self.assertIsNone(self.verdict("wesnoth: crashes"))


INDEX = {
    "swiftpm": {"version": "6.2.4"},
    "spideroak": {"version": "7.5.2"},
    "synergy": {"version": "1.14.6.19-stable"},
    "cassandra": {"version": "4.1.8"},
    "zitadel": {"version": "2.80.0"},
    "python313Packages.foo": {"version": "1.0"},
}
NAMES = {a.lower(): a for a in INDEX}


class UpdateRequests(unittest.TestCase):
    def check(self, title, master=None, updates=None, queue=None):
        return issues.check_update(
            title, INDEX, NAMES, master or {}, updates or {}, queue
        )

    def test_done(self):
        found = self.check("Update Request: swiftpm 5.8.0 → 6.1.0")
        self.assertEqual(
            found,
            {
                "package": "swiftpm",
                "from": "5.8.0",
                "to": "6.1.0",
                "now": "6.2.4",
                "verdict": "done",
            },
        )
        self.assertEqual(
            self.check("Update Request: spideroak 7.5.0 -> 7.5.2")["verdict"], "done"
        )

    def test_a_v_prefix_is_no_word(self):
        """v3.2.1 sorts below any number for Nix: compared without the v."""
        self.assertEqual(
            self.check("Update Request: Synergy 1.14.6.19-stable → v3.2.1")["verdict"],
            "open",
        )

    def test_open_with_its_pr(self):
        found = self.check(
            "Update Request: cassandra 4.1.8 → 5.0.4", updates={"cassandra": [406078]}
        )
        self.assertEqual((found["verdict"], found["prs"]), ("open", [406078]))

    def test_partly_and_master_first(self):
        self.assertEqual(
            self.check("Update Request: zitadel 2.71.7 → 4.0.0")["verdict"], "partly"
        )
        self.assertEqual(
            self.check(
                "Update Request: zitadel 2.71.7 → 4.0.0", master={"zitadel": "4.0.1"}
            )["verdict"],
            "done",
        )

    def test_others(self):
        self.assertEqual(
            self.check("Update request: nosuch 1 → 2")["verdict"], "notFound"
        )
        self.assertEqual(
            self.check("Update request: python3Packages.foo 0.9 → 1.0")["verdict"],
            "done",
        )
        self.assertEqual(
            self.check("Update request: cassandra 4.1.8 → unstable?")["verdict"],
            "notVersion",
        )
        self.assertIsNone(self.check("Update request: jaxlib with ROCm support"))

    def test_the_bots_queue_reaches_it(self):
        queue = {
            "cassandra": {"candidates": [["4.1.8", "5.0.6"]], "by": "2026-10-20"},
            "zitadel": {"candidates": [["2.80.0", "3.9.0"]]},
        }
        found = self.check("Update Request: cassandra 4.1.8 → v5.0.4", queue=queue)
        self.assertEqual(found["bot"], {"to": "5.0.6", "by": "2026-10-20"})
        # Not up to the version asked; and nothing once nixpkgs has it.
        self.assertNotIn(
            "bot", self.check("Update Request: zitadel 2.71.7 → 4.0.0", queue=queue)
        )
        done = {"swiftpm": {"candidates": [["6.2.4", "6.3.0"]]}}
        self.assertNotIn(
            "bot", self.check("Update Request: swiftpm 5.8.0 → 6.1.0", queue=done)
        )


if __name__ == "__main__":
    unittest.main()
