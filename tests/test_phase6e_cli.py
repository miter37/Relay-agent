from __future__ import annotations

import unittest

from relay.cli import _preprocess, build_parser


class Phase6eCLITests(unittest.TestCase):
    def test_lifecycle_cli_parsers(self):
        ns = build_parser().parse_args(_preprocess(["export", "--include-runs", "--out", "archive.zip"]))
        self.assertEqual(ns.command, "export")
        self.assertTrue(ns.include_runs)
        self.assertEqual(ns.out, "archive.zip")

        ns = build_parser().parse_args(_preprocess(["import", "archive.zip", "--conflict", "rename"]))
        self.assertEqual(ns.command, "import")
        self.assertEqual(ns.archive, "archive.zip")
        self.assertEqual(ns.conflict, "rename")

        ns = build_parser().parse_args(_preprocess(["receipt-schema"]))
        self.assertEqual(ns.command, "receipt-schema")


if __name__ == "__main__":
    unittest.main()
