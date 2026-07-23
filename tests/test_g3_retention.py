from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from relay.config import Config
from relay.db import Database
from relay.schedules.retention import ScheduleRetentionManager


class ScheduleRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.db.create_job(
            {
                "job_id": "source-1",
                "caller": "human",
                "submitted_via": "cli",
                "task_hash": "hash-source-1",
                "task_text": "A saved task",
                "task_preview": "A saved task",
                "title": "Saved task",
                "requested_worker": "codex",
                "format": "json",
                "profile": "web-research",
                "output_path": "/tmp/source-1.json",
                "artifact_path": "/tmp/source-1-artifacts",
                "fallback_enabled": 0,
                "request_json": '{"task":"A saved task","worker":"codex"}',
                "replayable": 1,
            }
        )
        self.root = self.home / "selected-output"
        self.root.mkdir(parents=True)
        self.db.create_schedule(
            {
                "schedule_id": "sch-1",
                "name": "Daily",
                "source_job_id": "source-1",
                "rule_json": '{"type":"daily","times":["09:00"],"timezone":"UTC"}',
                "timezone": "UTC",
                "overlap_policy": "skip",
                "missed_policy": "skip",
                "missed_grace_seconds": 43200,
                "input_root": str(self.home / "schedule-inputs" / "sch-1"),
                "output_root": str(self.root),
                "retention_json": '{"mode":"days","value":7}',
                "next_run_at_utc": "2026-07-24T09:00:00+00:00",
            }
        )

    def tearDown(self):
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def add_run(self, run_id: str, when: str, status: str) -> Path:
        run_root = self.root / run_id
        run_root.mkdir()
        result = run_root / "result.json"
        result.write_text("{}", encoding="utf-8")
        (run_root / ".relay-schedule-run.json").write_text(
            json.dumps({"schedule_id": "sch-1", "run_id": run_id}), encoding="utf-8"
        )
        self.db.insert_schedule_run(
            "sch-1",
            {
                "run_id": run_id,
                "occurrence_key": f"occ-{run_id}",
                "scheduled_for_utc": when,
                "scheduled_for_local": when,
                "trigger_type": "scheduled",
                "status": status,
                "output_path": str(result),
                "artifact_path": str(run_root / "artifacts"),
            },
        )
        return run_root

    def test_days_protects_active_and_newest_success_but_removes_old_run(self):
        old = self.add_run("old", "2026-07-01T09:00:00+00:00", "COMPLETED")
        newest = self.add_run("newest", "2026-07-22T09:00:00+00:00", "COMPLETED")
        active = self.add_run("active", "2026-07-01T10:00:00+00:00", "RUNNING")

        report = ScheduleRetentionManager(self.config, self.db).run(now=datetime(2026, 7, 23, tzinfo=UTC))

        self.assertIn(str(old), report["removed"])
        self.assertFalse(old.exists())
        self.assertTrue(newest.exists())
        self.assertTrue(active.exists())
        self.assertTrue(self.root.exists())

    def test_latest_runs_retains_newest_n_and_does_not_remove_root(self):
        self.db.update_schedule("sch-1", retention_json='{"mode":"latest_runs","value":2}')
        oldest = self.add_run("oldest", "2026-07-01T09:00:00+00:00", "FAILED")
        middle = self.add_run("middle", "2026-07-02T09:00:00+00:00", "FAILED")
        newest = self.add_run("newest", "2026-07-03T09:00:00+00:00", "FAILED")

        report = ScheduleRetentionManager(self.config, self.db).run(now=datetime(2026, 7, 23, tzinfo=UTC))

        self.assertIn(str(oldest), report["removed"])
        self.assertFalse(oldest.exists())
        self.assertTrue(middle.exists())
        self.assertTrue(newest.exists())
        self.assertTrue(self.root.is_dir())

    def test_forever_and_symlink_escape_are_safe(self):
        self.db.update_schedule("sch-1", retention_json='{"mode":"forever"}')
        kept = self.add_run("kept", "2020-01-01T09:00:00+00:00", "FAILED")
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        link = self.root / "escape"
        link.symlink_to(outside, target_is_directory=True)
        self.db.insert_schedule_run(
            "sch-1",
            {
                "run_id": "escape-run",
                "occurrence_key": "occ-escape",
                "scheduled_for_utc": "2020-01-02T09:00:00+00:00",
                "scheduled_for_local": "2020-01-02T09:00:00+00:00",
                "trigger_type": "scheduled",
                "status": "FAILED",
                "output_path": str(link / "result.json"),
                "artifact_path": str(link / "artifacts"),
            },
        )

        report = ScheduleRetentionManager(self.config, self.db).run(now=datetime(2026, 7, 23, tzinfo=UTC))

        self.assertTrue(kept.exists())
        self.assertTrue(outside.exists())
        self.assertTrue(any(item["code"] == "SCHEDULE_PATH_NOT_ALLOWED" for item in report["errors"]))

    def test_deletion_failure_is_reported_for_retry(self):
        target = self.add_run("retry", "2026-07-01T09:00:00+00:00", "FAILED")
        manager = ScheduleRetentionManager(self.config, self.db)
        with patch("relay.schedules.retention.shutil.rmtree", side_effect=OSError("busy")):
            report = manager.run(now=datetime(2026, 7, 23, tzinfo=UTC))

        self.assertTrue(target.exists())
        self.assertTrue(report["errors"])
        self.assertTrue(report["errors"][0]["retryable"])


if __name__ == "__main__":
    unittest.main()
