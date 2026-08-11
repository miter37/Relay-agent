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
                {
                    "title",
                    "submitted_via",
                    "task_preview",
                    "schedule_id",
                    "scheduled_for",
                    "replayable",
                    "trigger_type",
                    "task_id",
                    "task_snapshot_json",
                    "task_summary",
                    "result_summary",
                }
                <= columns
            )
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(
                {
                    "schedules",
                    "schedule_runs",
                    "tasks",
                    "projects",
                    "project_runs",
                    "routines",
                    "routine_runs",
                    "approvals",
                    "deliveries",
                    "notification_events",
                }
                <= tables
            )
            self.assertIn("receipt_schema_version", columns)
            task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            self.assertIn("task_summary", task_columns)
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
            artifact_columns = {row[1] for row in conn.execute("PRAGMA table_info(artifacts)")}
            self.assertTrue({"artifact_uid", "role", "producer_attempt_id", "producer"} <= artifact_columns)
            self.assertEqual(
                conn.execute(
                    "SELECT trigger_type,task_id,task_snapshot_json FROM jobs WHERE job_id='fixture-completed'"
                ).fetchone(),
                ("manual", None, None),
            )
            self.assertTrue(conn.execute("SELECT artifact_uid FROM artifacts").fetchone()[0])
            self.assertEqual(conn.execute("SELECT role FROM artifacts").fetchone()[0], "output")
            self.assertIsNone(conn.execute("SELECT producer_attempt_id FROM artifacts").fetchone()[0])
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

    def test_new_database_has_catalog_columns_and_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
                job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
                self.assertIn("task_summary", task_columns)
                self.assertTrue({"task_summary", "result_summary"} <= job_columns)
                task_indexes = {row[1] for row in conn.execute("PRAGMA index_list(tasks)")}
                job_indexes = {row[1] for row in conn.execute("PRAGMA index_list(jobs)")}
                self.assertIn("idx_tasks_catalog", task_indexes)
                self.assertIn("idx_jobs_catalog", job_indexes)
                project_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
                self.assertIn("project_summary", project_columns)
                project_indexes = {row[1] for row in conn.execute("PRAGMA index_list(projects)")}
                self.assertIn("idx_projects_catalog", project_indexes)

    def test_v12_to_v13_adds_catalog_columns_and_backfills_task_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            db = Database(path)
            db.create_task(
                {
                    "task_id": "legacy-task",
                    "name": "Legacy task",
                    "description": "A durable task summary.",
                    "instructions": "Long instructions.",
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
            )
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("ALTER TABLE tasks DROP COLUMN task_summary")
                conn.execute("ALTER TABLE jobs DROP COLUMN task_summary")
                conn.execute("ALTER TABLE jobs DROP COLUMN result_summary")
                conn.execute("PRAGMA user_version=12")
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
                self.assertEqual(
                    conn.execute("SELECT task_summary FROM tasks WHERE task_id='legacy-task'").fetchone()[0],
                    "A durable task summary.",
                )

    def test_v13_to_v14_adds_project_summary_and_backfills(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            db = Database(path)
            db.create_project(
                {
                    "project_id": "legacy-project",
                    "name": "Legacy project",
                    "description": "Legacy project description.",
                    "definition_json": '{"nodes":[],"connections":[],"output_selection":[]}',
                    "project_summary": "Legacy project description.",
                }
            )
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("ALTER TABLE projects DROP COLUMN project_summary")
                conn.execute("PRAGMA user_version=13")
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
                self.assertEqual(
                    conn.execute("SELECT project_summary FROM projects WHERE project_id='legacy-project'").fetchone()[
                        0
                    ],
                    "Legacy project description.",
                )

    def test_newer_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("PRAGMA user_version=99")
                conn.commit()
            with self.assertRaisesRegex(RelayError, "newer than supported"):
                Database(path)

    def test_migration_5_to_6_adds_tasks_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("PRAGMA user_version=5")
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertIn("tasks", tables)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)

    def test_migration_8_to_9_adds_approval_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("PRAGMA user_version=8")
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertIn("approvals", tables)
                self.assertIn("deliveries", tables)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)

    def test_migration_7_to_8_adds_routine_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("PRAGMA user_version=7")
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertIn("routines", tables)
                self.assertIn("routine_runs", tables)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)

    def test_migration_6_to_7_adds_project_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("PRAGMA user_version=6")
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertIn("projects", tables)
                self.assertIn("project_runs", tables)
                self.assertIn("project_run_steps", tables)
                self.assertIn("project_step_runs", tables)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
