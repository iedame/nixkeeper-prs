"""What a diff says (diffs.py)."""

import unittest

from nixkeeper_prs import diffs


def diff(*files):
    """A unified diff of (path, removed, added, new) files."""
    out = []
    for path, removed, added, new in files:
        out.append(f"diff --git a/{path} b/{path}")
        if new:
            out.append("new file mode 100644")
        out += [f"--- a/{path}", f"+++ b/{path}", "@@ -1,3 +1,3 @@ x", " context"]
        out += [f"-{line}" for line in removed] + [f"+{line}" for line in added]
    return ("\n".join(out) + "\n").encode()


PKG = "pkgs/by-name/fo/foo/package.nix"


class Facts(unittest.TestCase):
    def test_a_plain_version_bump(self):
        bump = diff(
            (
                PKG,
                ['  version = "1.0";', '    hash = "sha256-AAA=";'],
                ['  version = "1.1";', '    hash = "sha256-BBB=";'],
                False,
            )
        )
        self.assertTrue(diffs.facts(bump)["versionOnly"])
        more = diff(
            (
                PKG,
                ['  version = "1.0";'],
                ['  version = "1.1";', "  buildInputs = [ zlib ];"],
                False,
            )
        )
        self.assertNotIn("versionOnly", diffs.facts(more))

    def test_migrations(self):
        d = diff(
            (
                PKG,
                ['  rev = "v1.0";', '    sha256 = "0abc";', "  meta = with lib; {"],
                [
                    '  tag = "v1.0";',
                    '    hash = "sha256-AAA=";',
                    "  meta = {",
                    "  strictDeps = true;",
                    "  __structuredAttrs = true;",
                ],
                False,
            )
        )
        self.assertEqual(
            diffs.facts(d)["tags"],
            ["dropWithLib", "revToTag", "sriHash", "strictDeps", "structuredAttrs"],
        )

    def test_a_new_packages_hints_not_tags(self):
        d = diff(
            (
                "pkgs/by-name/ne/new/package.nix",
                [],
                [
                    "buildPythonPackage rec {",
                    '  rev = "abc";',
                    '  sha256 = "0abc";',
                    "  strictDeps = true;",
                    "  meta = with lib; { };",
                ],
                True,
            )
        )
        found = diffs.facts(d)
        self.assertEqual(
            found["hints"],
            ["noPythonImportsCheck", "oldHash", "rec", "revNotTag", "withLib"],
        )
        self.assertNotIn("tags", found)  # a new package declares them anyway
        # With the author added to the maintainer list: still no tags.
        both = d + diff(("maintainers/maintainer-list.nix", [], ["  me = { };"], False))
        self.assertNotIn("tags", diffs.facts(both))

    def test_cves_in_added_lines(self):
        d = diff(
            (
                PKG,
                [],
                [
                    '      url = "https://x/cve-2026-12345.patch";',
                    "  # CVE-2026-99999 fixed",
                ],
                False,
            )
        )
        self.assertEqual(diffs.facts(d)["cves"], ["CVE-2026-12345", "CVE-2026-99999"])

    def test_the_same_change_elsewhere(self):
        a = diff((PKG, ["  old"], ["  new"], False))
        b = a.replace(b"@@ -1,3 +1,3 @@ x\n context", b"@@ -40,3 +40,3 @@ y\n other")
        self.assertEqual(diffs.facts(a)["change"], diffs.facts(b)["change"])
        c = a.replace(b"+  new", b"+  newer")
        self.assertNotEqual(diffs.facts(a)["change"], diffs.facts(c)["change"])


if __name__ == "__main__":
    unittest.main()
