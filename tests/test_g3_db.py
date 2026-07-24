from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from relay.db import Database


def job_row(job_id: str = "job-1") -> dict:
    return {
        "job_id": job_id,
        "caller": "human",
        "submitted_via": "cli",
        "task_hash": f"hash-{job_id}",
        "task_text": "A saved task",
        "task_preview": "A saved task",
        "title": "Saved task",
        "requested_worker": "codex",
        "format": "json",
        "profile": "web-research",
        "output_path": f"/tmp/{job_id}.json",
        "artifact_path": f"/tmp/{job_id}-artifacts",
        "fallback_enabled": 0,
        "request_json": '{"task":"A saved task","worker":"codex"}',
        "replayable": 1,
    }


def schedule_row(schedule_id: str = "sch-1") -> dict:
    return {
        "schedule_id": schedule_id,
        "name": "Daily task",
        "source_job_id": "job-1",
        "rule_json": '{"type":"daily","times":["09:00"],"timezone":"Asia/Seoul"}',
        "timezone": "Asia/Seoul",
        "enabled": 1,
        "overlap_policy": "skip",
        "missed_policy": "skip",
        "missed_grace_seconds": 43200,
        "input_root": "/tmp/input",
        "output_root": "/tmp/output",
        "retention_json": '{"mode":"days","value":90}',
        "next_run_at_utc": "2026-07-24T00:00:00+00:00",
    }


def run_row(run_id: str = "run-1", occurrence_key: str = "occ-1") -> dict:
    return {
        "run_id": run_id,
        "occurrence_key": occurrence_key,
        "scheduled_for_utc": "2026-07-24T00:00:00+00:00",
        "scheduled_for_local": "2026-07-24T09:00:00+09:00",
        "trigger_type": "scheduled",
        "status": "PLANNED",
    }


class G3DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "relay.db")
        self.db.create_job(job_row())
        self.db.create_schedule(schedule_row())

    def tearDown(self):
        self.temp.cleanup()

    def test_schedule_crud_and_run_listing(self):
        schedule = self.db.get_schedule("sch-1")

        self.assertEqual(schedule["name"], "Daily task")
        self.assertEqual([row["schedule_id"] for row in self.db.list_schedules()], ["sch-1"])

        self.db.update_schedule("sch-1", enabled=0, next_run_at_utc=None)
        self.assertEqual(self.db.get_schedule("sch-1")["enabled"], 0)
        self.assertIsNone(self.db.get_schedule("sch-1")["next_run_at_utc"])

        self.assertTrue(self.db.insert_schedule_run("sch-1", run_row()))
        self.assertEqual(self.db.list_schedule_runs("sch-1")[0]["run_id"], "run-1")

    def test_occurrence_key_is_unique(self):
        self.assertTrue(self.db.insert_schedule_run("sch-1", run_row()))

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.insert_schedule_run("sch-1", run_row(run_id="run-2"))

    def test_active_jobs_for_schedule_and_linking(self):
        self.db.update_job("job-1", schedule_id="sch-1", status="QUEUED")
        self.db.insert_schedule_run("sch-1", run_row())
        self.db.link_schedule_run_job("run-1", "job-1", status="QUEUED")

        self.assertEqual(self.db.active_jobs_for_schedule("sch-1")[0]["job_id"], "job-1")
        self.assertEqual(self.db.get_schedule_run("run-1")["job_id"], "job-1")


if __name__ == "__main__":
    unittest.main()
