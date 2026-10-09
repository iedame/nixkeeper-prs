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


if __name__ == "__main__":
    unittest.main()
