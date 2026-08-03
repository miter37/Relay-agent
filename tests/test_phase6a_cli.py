from __future__ import annotations

import unittest

from relay.cli import build_parser


class Phase6aCLITests(unittest.TestCase):
    def test_approval_cli_parsers(self):
        ns = build_parser().parse_args(["approval", "list", "pr-123"])
        self.assertEqual(ns.command, "approval")
        self.assertEqual(ns.approval_command, "list")
        self.assertEqual(ns.project_run_id, "pr-123")

        ns = build_parser().parse_args(["approval", "show", "tok-456"])
        self.assertEqual(ns.token, "tok-456")

        ns = build_parser().parse_args(["approval", "approve", "pr-123", "tok-456", "--reviewer", "bob"])
        self.assertEqual(ns.reviewer, "bob")

        ns = build_parser().parse_args(["approval", "reject", "pr-123", "tok-456", "--reason", "bad"])
        self.assertEqual(ns.reason, "bad")

        ns = build_parser().parse_args(
            ["approval", "edit", "pr-123", "tok-456", "--file", "/tmp/edit.txt", "--role", "draft"]
        )
        self.assertEqual(ns.file, "/tmp/edit.txt")
        self.assertEqual(ns.role, "draft")


if __name__ == "__main__":
    unittest.main()
