from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database


class RoutineDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "relay.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_7_to_8_creates_routine_tables(self):
        with sqlite3.connect(self.path) as conn, conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"routines", "routine_runs"} <= tables)

    def test_routine_crud_and_soft_delete(self):
        self.db.create_routine(
            {
                "routine_id": "r-1",
                "name": "Daily HBM",
                "target_type": "task",
                "target_id": "T-1",
                "rule_json": "{}",
                "timezone": "Asia/Seoul",
                "enabled": 1,
                "overlap_policy": "skip",
                "missed_policy": "skip",
                "missed_grace_seconds": 43200,
                "version_policy": "latest",
                "next_run_at_utc": None,
            }
        )
        self.assertEqual(self.db.get_routine("r-1")["name"], "Daily HBM")
        self.assertEqual([r["routine_id"] for r in self.db.list_routines()], ["r-1"])
        self.db.update_routine("r-1", name="Renamed")
        self.assertEqual(self.db.get_routine("r-1")["name"], "Renamed")
        self.assertTrue(self.db.soft_delete_routine("r-1"))
        self.assertIsNotNone(self.db.get_routine("r-1")["deleted_at"])
        self.assertEqual(self.db.list_routines(), [])
        self.assertFalse(self.db.soft_delete_routine("r-1"))

    def test_claim_routine_occurrence_is_atomic(self):
        self.db.create_routine(
            {
                "routine_id": "r-1",
                "name": "R",
                "target_type": "task",
                "target_id": "T-1",
                "rule_json": "{}",
                "timezone": "Asia/Seoul",
                "enabled": 1,
                "overlap_policy": "skip",
                "missed_policy": "skip",
                "missed_grace_seconds": 43200,
                "version_policy": "latest",
                "next_run_at_utc": None,
            }
        )
        run = {
            "run_id": "rr-1",
            "occurrence_key": "2026-08-04T00:00",
            "scheduled_for_utc": "2026-08-04T00:00:00+00:00",
            "scheduled_for_local": "2026-08-04T09:00:00+09:00",
            "trigger_type": "routine",
            "status": "pending",
            "target_type": "task",
        }
        self.assertTrue(self.db.claim_routine_occurrence("r-1", run))
        self.assertFalse(self.db.claim_routine_occurrence("r-1", {**run, "run_id": "rr-2"}))
        runs = self.db.list_routine_runs(routine_id="r-1")
        self.assertEqual(len(runs), 1)

    def test_active_runs_for_routine(self):
        self.db.create_routine(
            {
                "routine_id": "r-1",
                "name": "R",
                "target_type": "task",
                "target_id": "T-1",
                "rule_json": "{}",
                "timezone": "Asia/Seoul",
                "enabled": 1,
                "overlap_policy": "skip",
                "missed_policy": "skip",
                "missed_grace_seconds": 43200,
                "version_policy": "latest",
                "next_run_at_utc": None,
            }
        )
        run_pending = {
            "run_id": "rr-1",
            "occurrence_key": "a",
            "scheduled_for_utc": "2026-08-04T00:00:00+00:00",
            "scheduled_for_local": "2026-08-04T09:00:00+09:00",
            "trigger_type": "routine",
            "status": "pending",
            "target_type": "task",
        }
        run_completed = {**run_pending, "run_id": "rr-2", "occurrence_key": "b", "status": "completed"}
        self.db.claim_routine_occurrence("r-1", run_pending)
        self.db.claim_routine_occurrence("r-1", run_completed)
        active = self.db.active_runs_for_routine("r-1")
        self.assertEqual([r["run_id"] for r in active], ["rr-1"])


if __name__ == "__main__":
    unittest.main()
