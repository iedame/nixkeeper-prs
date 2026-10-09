"""Nix's version order (nixversions.py), against Nix itself: every pair in
tests/data/nix-versions.tsv with what builtins.compareVersions said."""

import os
import unittest

from nixkeeper_prs import nixversions

DATA = os.path.join(os.path.dirname(__file__), "data", "nix-versions.tsv")


class AsNix(unittest.TestCase):
    def test_every_pair_as_nix_said(self):
        with open(DATA, encoding="utf-8") as f:
            rows = [
                line.rstrip("\n").split("\t") for line in f if not line.startswith("#")
            ]
        self.assertGreater(len(rows), 2000)
        wrong = [
            (a, b, int(want), nixversions.compare(a, b))
            for a, b, want in rows
            if nixversions.compare(a, b) != int(want)
        ]
        self.assertEqual(wrong, [])

    def test_the_rules(self):
        self.assertEqual(nixversions.compare("1.0pre1", "1.0"), -1)  # pre
        self.assertEqual(nixversions.compare("2.3a", "2.3.1"), -1)  # a word < a number
        self.assertEqual(nixversions.compare("1.0", "1.0.0"), -1)  # ran out < a number
        self.assertEqual(nixversions.compare("1.2", "1.2"), 0)
        # A component over a C int compares as a word.
        self.assertEqual(nixversions.compare("0.3.2", "24338890799c4b"), 1)
        self.assertEqual(nixversions.compare("1²", "1"), 1)  # not a digit for C


if __name__ == "__main__":
    unittest.main()
