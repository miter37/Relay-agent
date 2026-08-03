from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database


class Phase6dDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "relay.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_9_to_10_creates_notification_events_table(self):
        with sqlite3.connect(self.path) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("notification_events", tables)

    def test_notification_event_crud(self):
        self.db.create_notification_event({
            "event_id": "ne-1",
            "routine_id": "r-1",
            "trigger_type": "on_failure",
            "sink_url": "http://127.0.0.1:8080/hook",
            "status": "delivered",
            "status_code": 200,
            "attempt": 1,
        })
        events = self.db.list_notification_events(routine_id="r-1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "ne-1")
        self.assertEqual(events[0]["status_code"], 200)


if __name__ == "__main__":
    unittest.main()
