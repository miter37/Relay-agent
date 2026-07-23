from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database
from relay.errors import RelayError

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"


class MigrationTests(unittest.TestCase):
    def copy_fixture(self, name: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        target = Path(temp.name) / name
        shutil.copy2(FIXTURES / name, target)
        return temp, target

    def test_empty_0_5_fixture_migrates(self):
        temp, path = self.copy_fixture("relay-0.5.0-empty.db")
        self.addCleanup(temp.cleanup)

        db = Database(path)

        with closing(sqlite3.connect(path)) as conn, conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            self.assertTrue(
                {"title", "submitted_via", "task_preview", "schedule_id", "scheduled_for", "replayable"} <= columns
            )
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"schedules", "schedule_runs"} <= tables)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)
        self.assertIsNotNone(db.last_backup_path)
        self.assertTrue(db.last_backup_path and db.last_backup_path.exists())

    def test_populated_fixture_preserves_rows_values_and_relationships(self):
        temp, path = self.copy_fixture("relay-0.5.0-populated.db")
        self.addCleanup(temp.cleanup)

        Database(path)

        with closing(sqlite3.connect(path)) as conn, conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM capability_audits").fetchone()[0], 1)
            row = conn.execute(
                "SELECT job_id,status,request_id,output_path,submitted_via,replayable FROM jobs "
                "WHERE job_id='fixture-completed'"
            ).fetchone()
            self.assertEqual(
                row[:4],
                (
                    "fixture-completed",
                    "COMPLETED",
                    "fixture-request-1",
                    "D:/RelayFixture/results/completed.json",
                ),
            )
            self.assertEqual(row[4:], ("legacy", 1))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM attempts WHERE job_id='fixture-failed'").fetchone()[0], 2
            )

    def test_migration_is_idempotent_and_reopens(self):
        temp, path = self.copy_fixture("relay-0.5.0-populated.db")
        self.addCleanup(temp.cleanup)

        first = Database(path)
        backup = first.last_backup_path
        second = Database(path)

        self.assertIsNone(second.last_backup_path)
        self.assertTrue(backup and backup.exists())
        with closing(sqlite3.connect(path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 3)

    def test_fixture_checksum_is_stable(self):
        expected = {
            "relay-0.5.0-empty.db": "14f3c53b32dd93eea382214783ef8a961d1d3c797a278a503d5e9451cc574f09",
            "relay-0.5.0-populated.db": "b08e3b2dc00bd3fde5b34b16b2e59ec2c11af7fd2f5166454af48aeda4a5ade2",
        }
        for name, checksum in expected.items():
            with self.subTest(name=name):
                digest = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
                self.assertEqual(digest, checksum)

    def test_new_database_starts_at_current_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            db = Database(path)
            self.assertIsNone(db.last_backup_path)
            with closing(sqlite3.connect(path)) as conn, conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)

    def test_newer_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("PRAGMA user_version=99")
                conn.commit()
            with self.assertRaisesRegex(RelayError, "newer than supported"):
                Database(path)


if __name__ == "__main__":
    unittest.main()
