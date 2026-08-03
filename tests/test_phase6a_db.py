from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database


class Phase6aDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "relay.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def _seed_project_run(self):
        self.db.create_project({
            "project_id": "p-1",
            "name": "P",
            "description": None,
            "version": 1,
            "definition_json": "{}",
        })
        self.db.create_project_run({
            "project_run_id": "pr-1",
            "project_id": "p-1",
            "project_version": 1,
            "project_snapshot_json": "{}",
            "status": "running",
            "trigger_type": "manual",
            "submitted_via": "cli",
        })

    def test_migration_8_to_9_creates_approval_tables(self):
        with sqlite3.connect(self.path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"approvals", "deliveries"} <= tables)

    def test_approval_crud_and_token_lookup(self):
        self._seed_project_run()
        self.db.create_approval({
            "approval_id": "app-1",
            "project_run_id": "pr-1",
            "node_id": "n-1",
            "token": "tok-123",
            "status": "pending",
        })
        app = self.db.get_approval("tok-123")
        self.assertIsNotNone(app)
        self.assertEqual(app["project_run_id"], "pr-1")
        self.assertEqual(app["status"], "pending")

        listed = self.db.list_approvals("pr-1")
        self.assertEqual([a["approval_id"] for a in listed], ["app-1"])

        self.db.update_approval("tok-123", status="approved", reviewer="alice", decided_at="2026-08-04T00:00:00Z")
        updated = self.db.get_approval("tok-123")
        self.assertEqual(updated["status"], "approved")
        self.assertEqual(updated["reviewer"], "alice")

    def test_delivery_crud_and_listing(self):
        self._seed_project_run()
        self.db.create_delivery({
            "delivery_id": "del-1",
            "project_run_id": "pr-1",
            "approval_id": None,
            "kind": "folder",
            "target_path": "/tmp/out",
            "artifact_uid": "art-1",
            "status": "completed",
        })
        deliveries = self.db.list_deliveries("pr-1")
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["delivery_id"], "del-1")
        self.assertEqual(deliveries[0]["target_path"], "/tmp/out")


if __name__ == "__main__":
    unittest.main()
