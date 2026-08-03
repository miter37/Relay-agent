from __future__ import annotations

import unittest

from relay.cli import _preprocess, build_parser


class Phase6cCLITests(unittest.TestCase):
    def test_quality_and_semantic_cli_parsers(self):
        ns = build_parser().parse_args(_preprocess(["search", "semantic", "HBM supply", "--kind", "runs"]))
        self.assertEqual(ns.command, "search-semantic")
        self.assertEqual(ns.query, "HBM supply")

        ns = build_parser().parse_args(_preprocess(["quality", "attention", "--status", "low"]))
        self.assertEqual(ns.command, "quality")
        self.assertEqual(ns.quality_command, "attention")
        self.assertEqual(ns.status, "low")


if __name__ == "__main__":
    unittest.main()
