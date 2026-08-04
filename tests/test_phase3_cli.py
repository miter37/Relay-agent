from __future__ import annotations

import unittest

from relay.cli import _preprocess, build_parser


class TaskCLITests(unittest.TestCase):
    def test_task_create_parses_name_and_instructions(self):
        ns = build_parser().parse_args(
            _preprocess(["task", "create", "--name", "Report", "--instructions", "Write it"])
        )
        self.assertEqual(ns.command, "task")
        self.assertEqual(ns.task_command, "create")
        self.assertEqual(ns.name, "Report")
        self.assertEqual(ns.instructions, "Write it")

    def test_task_create_parses_task_file_and_overrides(self):
        ns = build_parser().parse_args(
            _preprocess(
                [
                    "task",
                    "create",
                    "--name",
                    "Report",
                    "--task-file",
                    "prompt.md",
                    "--worker",
                    "codex",
                    "--no-fallback",
                    "--timeout",
                    "120",
                    "--format",
                    "json",
                ]
            )
        )
        self.assertEqual(ns.task_file, "prompt.md")
        self.assertEqual(ns.worker, "codex")
        self.assertFalse(ns.fallback)
        self.assertEqual(ns.timeout, 120)
        self.assertEqual(ns.format, "json")

    def test_task_create_and_update_parse_catalog_summary(self):
        create = build_parser().parse_args(
            _preprocess(["task", "create", "--name", "Report", "--summary", "Summarize weekly evidence"])
        )
        update = build_parser().parse_args(
            _preprocess(["task", "update", "task-101", "--summary", "Compare weekly evidence"])
        )
        self.assertEqual(create.task_summary, "Summarize weekly evidence")
        self.assertEqual(update.task_summary, "Compare weekly evidence")

    def test_task_list_and_show_and_runs(self):
        ns_list = build_parser().parse_args(_preprocess(["task", "list", "--name", "HBM", "--machine"]))
        self.assertEqual(ns_list.task_command, "list")
        self.assertEqual(ns_list.name, "HBM")
        self.assertTrue(ns_list.machine)

        ns_show = build_parser().parse_args(_preprocess(["task", "show", "task-101"]))
        self.assertEqual(ns_show.task_command, "show")
        self.assertEqual(ns_show.task_id, "task-101")

        ns_runs = build_parser().parse_args(_preprocess(["task", "runs", "task-101", "--limit", "10"]))
        self.assertEqual(ns_runs.task_command, "runs")
        self.assertEqual(ns_runs.limit, 10)

    def test_task_run_parses_overrides(self):
        ns = build_parser().parse_args(_preprocess(["task", "run", "task-101", "--worker", "claude", "--machine"]))
        self.assertEqual(ns.command, "task")
        self.assertEqual(ns.task_command, "run")
        self.assertEqual(ns.task_id, "task-101")
        self.assertEqual(ns.worker, "claude")

    def test_run_save_as_task_parses_name(self):
        ns = build_parser().parse_args(_preprocess(["run", "save-as-task", "job-101", "--name", "Saved Task"]))
        self.assertEqual(ns.command, "task")
        self.assertEqual(ns.task_command, "save-as-task")
        self.assertEqual(ns.job_id, "job-101")
        self.assertEqual(ns.name, "Saved Task")

    def test_schedule_uses_task_run_as_canonical_source_option(self):
        ns = build_parser().parse_args(
            [
                "schedule",
                "create",
                "--from-task-run",
                "task-run-101",
                "--name",
                "Daily report",
                "--type",
                "daily",
                "--time",
                "09:00",
            ]
        )
        self.assertEqual(ns.source_job_id, "task-run-101")

    def test_legacy_schedule_source_option_remains_accepted(self):
        ns = build_parser().parse_args(
            [
                "schedule",
                "create",
                "--from-job",
                "job-101",
                "--name",
                "Daily report",
                "--type",
                "daily",
                "--time",
                "09:00",
            ]
        )
        self.assertEqual(ns.source_job_id, "job-101")


if __name__ == "__main__":
    unittest.main()
