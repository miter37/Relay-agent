from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database
from relay.errors import RelayError


class Phase4DBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "relay.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_6_to_7_creates_project_tables(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"projects", "project_runs", "project_run_steps", "project_step_runs"} <= tables)

    def test_project_crud_and_soft_delete(self):
        self.db.create_project(
            {
                "project_id": "p-1",
                "name": "Weekly",
                "description": None,
                "version": 1,
                "definition_json": "{}",
            }
        )
        self.assertEqual(self.db.get_project("p-1")["name"], "Weekly")
        self.assertEqual([p["project_id"] for p in self.db.list_projects()], ["p-1"])
        self.db.update_project("p-1", name="Renamed")
        self.assertEqual(self.db.get_project("p-1")["name"], "Renamed")
        self.assertEqual(self.db.get_project("p-1")["version"], 2)
        self.assertTrue(self.db.soft_delete_project("p-1"))
        self.assertEqual(self.db.get_project("p-1")["version"], 3)
        self.assertFalse(self.db.soft_delete_project("p-1"))
        self.assertIsNotNone(self.db.get_project("p-1")["deleted_at"])
        self.assertEqual(self.db.list_projects(), [])

    def test_project_run_and_step_round_trip(self):
        self.db.create_project(
            {
                "project_id": "p-1",
                "name": "P",
                "description": None,
                "version": 1,
                "definition_json": '{"nodes":[]}',
            }
        )
        self.db.create_project_run(
            {
                "project_run_id": "pr-1",
                "project_id": "p-1",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "accepted",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )
        self.db.create_or_update_project_step(
            {
                "project_run_id": "pr-1",
                "node_id": "n1",
                "task_id": "T-1",
                "task_version": 1,
                "status": "ready",
            }
        )
        step = self.db.get_project_step("pr-1", "n1")
        self.assertEqual(step["status"], "ready")
        self.assertEqual([s["node_id"] for s in self.db.list_project_steps("pr-1")], ["n1"])

    def test_append_project_step_run_is_unique_on_task_run(self):
        self.db.create_project(
            {
                "project_id": "p-1",
                "name": "P",
                "description": None,
                "version": 1,
                "definition_json": "{}",
            }
        )
        self.db.create_project_run(
            {
                "project_run_id": "pr-1",
                "project_id": "p-1",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "running",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )
        self.db.create_or_update_project_step(
            {
                "project_run_id": "pr-1",
                "node_id": "n1",
                "task_id": "T-1",
                "task_version": 1,
                "status": "running",
                "active_task_run_id": "job-1",
            }
        )
        self.db.append_project_step_run("pr-1", "n1", "job-1", None)
        with self.assertRaisesRegex(RelayError, "STEP_RUN_DUPLICATE"):
            self.db.append_project_step_run("pr-1", "n1", "job-1", None)

    def test_claim_ready_steps_is_atomic(self):
        self.db.create_project(
            {
                "project_id": "p-1",
                "name": "P",
                "description": None,
                "version": 1,
                "definition_json": "{}",
            }
        )
        self.db.create_project_run(
            {
                "project_run_id": "pr-1",
                "project_id": "p-1",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "running",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )
        for node_id, status in [("a", "ready"), ("b", "ready"), ("c", "queued")]:
            self.db.create_or_update_project_step(
                {
                    "project_run_id": "pr-1",
                    "node_id": node_id,
                    "task_id": f"T-{node_id}",
                    "task_version": 1,
                    "status": status,
                }
            )
        claimed = self.db.claim_ready_steps("pr-1", "ready", "claimed")
        self.assertEqual({n for _, n in claimed}, {"a", "b"})
        self.db.claim_ready_steps("pr-1", "ready", "claimed")
        self.assertEqual(self.db.get_project_step("pr-1", "a")["status"], "claimed")
        self.assertEqual(self.db.get_project_step("pr-1", "b")["status"], "claimed")
        self.assertEqual(self.db.get_project_step("pr-1", "c")["status"], "queued")


if __name__ == "__main__":
    unittest.main()
