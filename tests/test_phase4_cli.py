from __future__ import annotations

import unittest

from relay.cli import build_parser


class ProjectCLITests(unittest.TestCase):
    def test_project_create_from_file(self):
        ns = build_parser().parse_args(["project", "create", "--file", "project.json"])
        self.assertEqual(ns.command, "project")
        self.assertEqual(ns.project_command, "create")
        self.assertEqual(ns.file, "project.json")

    def test_project_show_and_list(self):
        ns_show = build_parser().parse_args(["project", "show", "p-1"])
        self.assertEqual(ns_show.project_command, "show")
        self.assertEqual(ns_show.project_id, "p-1")
        ns_list = build_parser().parse_args(["project", "list", "--name", "weekly", "--machine"])
        self.assertTrue(ns_list.machine)

    def test_project_run_with_inputs(self):
        ns = build_parser().parse_args([
            "project", "run", "p-1",
            "--input", "collect:A1=ARTIFACT-UID",
            "--input", "analyze:A2=ARTIFACT-2",
            "--machine",
        ])
        self.assertEqual(ns.project_command, "run")
        self.assertEqual(ns.input, ["collect:A1=ARTIFACT-UID", "analyze:A2=ARTIFACT-2"])

    def test_project_run_subcommands(self):
        ns = build_parser().parse_args(["project-run", "show", "pr-1"])
        self.assertEqual(ns.command, "project-run")
        self.assertEqual(ns.project_run_command, "show")
        self.assertEqual(ns.project_run_id, "pr-1")

        ns = build_parser().parse_args(["project-run", "retry", "pr-1", "--from-node", "analyze", "--worker", "codex"])
        self.assertEqual(ns.from_node, "analyze")
        self.assertEqual(ns.worker, "codex")

        ns = build_parser().parse_args(["project-run", "cancel", "pr-1"])
        self.assertEqual(ns.project_run_command, "cancel")


if __name__ == "__main__":
    unittest.main()
