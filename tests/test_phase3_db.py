from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.db import Database


class TaskDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "relay.db")

    def tearDown(self):
        self.temp.cleanup()

    def _row(self, **overrides):
        base = {
            "task_id": "task-1",
            "name": "Weekly report",
            "description": None,
            "task_summary": None,
            "instructions": "Write a weekly report",
            "default_worker": "auto",
            "fallback_enabled": 1,
            "timeout_seconds": None,
            "profile": "web-research",
            "result_format": "json",
            "input_schema": None,
            "output_contract": None,
            "validation_policy": None,
            "version": 1,
        }
        base.update(overrides)
        return base

    def test_create_and_get_task(self):
        self.db.create_task(self._row())
        task = self.db.get_task("task-1")
        self.assertEqual(task["name"], "Weekly report")
        self.assertEqual(task["version"], 1)

    def test_task_summary_persists_and_updates(self):
        self.db.create_task(self._row(task_summary="Collect weekly HBM evidence."))
        self.db.update_task("task-1", task_summary="Compare weekly HBM evidence.")
        self.assertEqual(self.db.get_task("task-1")["task_summary"], "Compare weekly HBM evidence.")

    def test_get_missing_task_returns_none(self):
        self.assertIsNone(self.db.get_task("nope"))

    def test_list_tasks_filters_by_name_and_orders_newest(self):
        self.db.create_task(self._row(task_id="task-1", name="Alpha"))
        self.db.create_task(self._row(task_id="task-2", name="Beta report"))
        names = [t["name"] for t in self.db.list_tasks()]
        self.assertEqual(names, ["Beta report", "Alpha"])
        filtered = self.db.list_tasks(name="report")
        self.assertEqual([t["task_id"] for t in filtered], ["task-2"])

    def test_update_task_bumps_version_and_merges_fields(self):
        self.db.create_task(self._row())
        self.db.update_task("task-1", name="Renamed", instructions="New instructions")
        task = self.db.get_task("task-1")
        self.assertEqual(task["name"], "Renamed")
        self.assertEqual(task["instructions"], "New instructions")
        self.assertEqual(task["version"], 2)

    def test_delete_task_returns_true_and_removes_row(self):
        self.db.create_task(self._row())
        self.assertTrue(self.db.delete_task("task-1"))
        self.assertIsNone(self.db.get_task("task-1"))
        self.assertFalse(self.db.delete_task("task-1"))

    def test_runs_for_task_lists_linked_jobs(self):
        self.db.create_task(self._row())
        for jid, tid in [("job-a", "task-1"), ("job-b", "task-1"), ("job-c", None)]:
            self.db.create_job(
                {
                    "job_id": jid,
                    "caller": "human",
                    "submitted_via": "cli",
                    "task_hash": "h",
                    "requested_worker": "auto",
                    "format": "json",
                    "profile": "web-research",
                    "output_path": "o",
                    "artifact_path": "a",
                    "status": "QUEUED",
                    "request_json": "{}",
                    "task_id": tid,
                }
            )
        runs = self.db.runs_for_task("task-1")
        self.assertEqual([r["job_id"] for r in runs], ["job-b", "job-a"])


if __name__ == "__main__":
    unittest.main()
