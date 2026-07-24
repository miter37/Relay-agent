from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from relay.api import job_detail
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.schedules.runtime import ScheduleRuntime
from relay.schedules.service import ScheduleService


class G4ApiReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ScheduleService(self.config, self.db, self.engine)
        source, _ = self.engine.create_job(JobRequest(task="A saved report", worker="codex"), queued=True)
        self.db.update_job(
            source["job_id"],
            status="COMPLETED",
            result_status="complete",
            completed_at="2026-07-23T00:00:00+00:00",
        )
        self.source_id = source["job_id"]

    def tearDown(self):
        self.temp.cleanup()

    def create_schedule(self, **payload):
        return self.service.create_from_job(
            self.source_id,
            {
                "name": payload.pop("name", "Daily report"),
                "rule": payload.pop("rule", {"type": "daily", "times": ["09:00"], "timezone": "UTC"}),
                **payload,
            },
        )

    def test_runtime_links_output_paths_to_schedule_run(self):
        schedule = self.create_schedule()
        self.db.update_schedule(schedule["schedule_id"], next_run_at_utc="2026-07-23T08:00:00+00:00")

        result = ScheduleRuntime(self.config, self.db, self.engine).tick(datetime(2026, 7, 23, 9, 0, tzinfo=UTC))

        self.assertEqual(result["queued"], 1)
        run = self.db.list_schedule_runs(schedule["schedule_id"])[0]
        self.assertTrue(run["output_path"])
        self.assertTrue(run["artifact_path"])
        self.assertEqual(Path(run["output_path"]).parent.parent, Path(schedule["output_root"]))

    def test_schedule_runs_read_repair_terminal_job_status(self):
        schedule = self.create_schedule()
        self.db.update_schedule(schedule["schedule_id"], next_run_at_utc="2026-07-23T08:00:00+00:00")
        ScheduleRuntime(self.config, self.db, self.engine).tick(datetime(2026, 7, 23, 9, 0, tzinfo=UTC))
        run = self.db.list_schedule_runs(schedule["schedule_id"])[0]
        self.db.update_job(run["job_id"], status="COMPLETED", result_status="complete")

        visible = self.service.runs(schedule["schedule_id"])[0]

        self.assertEqual(visible["status"], "COMPLETED")

    def test_job_detail_exposes_schedule_eligibility(self):
        detail = job_detail(self.engine, self.source_id)

        self.assertTrue(detail["actions"]["can_schedule"])
        self.assertIsNone(detail["actions"].get("schedule_reason"))

    def test_completed_job_can_open_schedule_editor_before_isolation_acknowledgement(self):
        self.config.set("service_isolation_acknowledged", False)

        detail = job_detail(self.engine, self.source_id)

        self.assertTrue(detail["actions"]["can_schedule"])
        self.assertTrue(detail["actions"]["schedule_requires_isolation"])

    def test_preview_honors_start_and_end_bounds(self):
        preview = self.service.preview(
            {
                "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"},
                "after_utc": "2026-07-20T00:00:00+00:00",
                "starts_at_utc": "2026-07-22T00:00:00+00:00",
                "ends_at_utc": "2026-07-23T00:00:00+00:00",
                "limit": 5,
            }
        )

        self.assertEqual([item["utc"] for item in preview], ["2026-07-22T09:00:00+00:00"])

    def test_update_accepts_runtime_bounds_and_future_output_root(self):
        schedule = self.create_schedule()
        root = self.home / "future-output"

        updated = self.service.update(
            schedule["schedule_id"],
            {
                "starts_at_utc": "2026-07-24T00:00:00+00:00",
                "ends_at_utc": "2026-08-24T00:00:00+00:00",
                "missed_grace_seconds": 3600,
                "output_root": str(root),
            },
        )

        self.assertEqual(updated["starts_at_utc"], "2026-07-24T00:00:00+00:00")
        self.assertEqual(updated["ends_at_utc"], "2026-08-24T00:00:00+00:00")
        self.assertEqual(updated["missed_grace_seconds"], 3600)
        self.assertEqual(Path(updated["output_root"]), root / schedule["schedule_id"])

    def test_copy_schedule_clones_immutable_snapshot(self):
        schedule = self.create_schedule()

        copied = self.service.copy(schedule["schedule_id"], {"name": "Copied report"})

        self.assertNotEqual(copied["schedule_id"], schedule["schedule_id"])
        self.assertEqual(copied["name"], "Copied report")
        self.assertNotEqual(copied["input_root"], schedule["input_root"])
        self.assertEqual(
            (Path(copied["input_root"]) / "request.md").read_text(encoding="utf-8"),
            (Path(schedule["input_root"]) / "request.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
