from __future__ import annotations

import unittest

from relay.cli import build_parser


class Phase6bCLITests(unittest.TestCase):
    def test_compare_cli_parsers(self):
        ns = build_parser().parse_args(["compare", "runs", "job-1", "job-2"])
        self.assertEqual(ns.command, "compare")
        self.assertEqual(ns.compare_command, "runs")
        self.assertEqual(ns.a_run_id, "job-1")
        self.assertEqual(ns.b_run_id, "job-2")

        ns = build_parser().parse_args(["compare", "artifacts", "art-1", "art-2", "--max-bytes", "1000"])
        self.assertEqual(ns.compare_command, "artifacts")
        self.assertEqual(ns.a_uid, "art-1")
        self.assertEqual(ns.b_uid, "art-2")
        self.assertEqual(ns.max_bytes, 1000)

    def test_project_run_reexecute_parser(self):
        ns = build_parser().parse_args(
            ["project-run", "reexecute", "pr-123", "--from-node", "analyze", "--worker", "codex"]
        )
        self.assertEqual(ns.command, "project-run")
        self.assertEqual(ns.project_run_command, "reexecute")
        self.assertEqual(ns.project_run_id, "pr-123")
        self.assertEqual(ns.from_node, "analyze")
        self.assertEqual(ns.worker, "codex")


if __name__ == "__main__":
    unittest.main()
