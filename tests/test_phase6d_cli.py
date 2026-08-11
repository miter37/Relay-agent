from __future__ import annotations

import unittest

from relay.cli import _preprocess, build_parser


class Phase6dCLITests(unittest.TestCase):
    def test_observability_cli_parsers(self):
        ns = build_parser().parse_args(_preprocess(["attention", "list", "--kind", "failed_job"]))
        self.assertEqual(ns.command, "attention")
        self.assertEqual(ns.attention_command, "list")

        ns = build_parser().parse_args(_preprocess(["operations", "routines"]))
        self.assertEqual(ns.command, "operations")
        self.assertEqual(ns.operations_command, "routines")

        ns = build_parser().parse_args(_preprocess(["operations", "projects"]))
        self.assertEqual(ns.command, "operations")
        self.assertEqual(ns.operations_command, "projects")

        ns = build_parser().parse_args(_preprocess(["notify", "test", "--url", "http://127.0.0.1:8080/hook"]))
        self.assertEqual(ns.command, "notify")
        self.assertEqual(ns.notify_command, "test")


if __name__ == "__main__":
    unittest.main()
