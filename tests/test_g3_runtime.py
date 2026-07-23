from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.schedules.runtime import ScheduleRuntime
from relay.schedules.service import ScheduleService


class G3RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ScheduleService(self.config, self.db, self.engine)
        source, _ = self.engine.create_job(JobRequest(task="Scheduled task", worker="codex"), queued=True)
        self.db.update_job(
            source["job_id"], status="COMPLETED", result_status="complete", completed_at="2026-07-23T00:00:00+00:00"
        )
        self.source_id = source["job_id"]

    def tearDown(self):
        self.temp.cleanup()

    def make_schedule(self, rule=None, **options):
        result = self.service.create_from_job(
            self.source_id,
            {
                "name": options.pop("name", "Runtime schedule"),
                "rule": rule or {"type": "daily", "times": ["09:00"], "timezone": "UTC"},
                **options,
            },
        )
        self.db.update_schedule(result["schedule_id"], next_run_at_utc="2026-07-23T08:00:00+00:00")
        return result["schedule_id"]

    def test_due_schedule_creates_normal_queued_job_and_links_run(self):
        schedule_id = self.make_schedule()
        runtime = ScheduleRuntime(self.config, self.db, self.engine)

        result = runtime.tick(datetime(2026, 7, 23, 9, 0, tzinfo=UTC))

        self.assertEqual(result["queued"], 1)
        jobs = self.db.active_jobs_for_schedule(schedule_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["caller"], "schedule")
        self.assertEqual(jobs[0]["submitted_via"], "schedule")
        self.assertIn("schedule-outputs", Path(jobs[0]["output_path"]).parts)
        run = self.db.list_schedule_runs(schedule_id)[0]
        self.assertEqual(run["job_id"], jobs[0]["job_id"])
        self.assertEqual(run["status"], "QUEUED")

    def test_repeated_ticks_claim_one_occurrence(self):
        schedule_id = self.make_schedule()
        runtime = ScheduleRuntime(self.config, self.db, self.engine)
        results = []

        threads = [
            threading.Thread(target=lambda: results.append(runtime.tick(datetime(2026, 7, 23, 9, 0, tzinfo=UTC))))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(self.db.list_schedule_runs(schedule_id)), 1)
        self.assertEqual(len(self.db.active_jobs_for_schedule(schedule_id)), 1)

    def test_overlap_skip_records_skipped_run(self):
        schedule_id = self.make_schedule(overlap_policy="skip")
        active, _ = self.engine.create_job(
            JobRequest(task="Existing scheduled task", worker="codex"), queued=True, submitted_via="schedule"
        )
        self.db.update_job(active["job_id"], caller="schedule", schedule_id=schedule_id, status="RUNNING")
        runtime = ScheduleRuntime(self.config, self.db, self.engine)

        result = runtime.tick(datetime(2026, 7, 23, 9, 0, tzinfo=UTC))

        self.assertEqual(result["skipped"], 1)
        self.assertEqual(self.db.list_schedule_runs(schedule_id)[0]["status"], "SKIPPED")

    def test_one_time_schedule_disables_after_queue(self):
        schedule_id = self.make_schedule()
        self.db.update_schedule(
            schedule_id,
            rule_json=json.dumps({"type": "once", "run_at_local": "2026-07-23T08:00:00", "timezone": "UTC"}),
            timezone="UTC",
        )
        runtime = ScheduleRuntime(self.config, self.db, self.engine)

        runtime.tick(datetime(2026, 7, 23, 9, 0, tzinfo=UTC))

        self.assertEqual(self.db.get_schedule(schedule_id)["enabled"], 0)

    def test_manual_run_now_pending_run_is_queued(self):
        schedule_id = self.make_schedule()
        manual = self.service.run_now(schedule_id)
        runtime = ScheduleRuntime(self.config, self.db, self.engine)

        runtime.tick(datetime.now(UTC))

        self.assertEqual(self.db.get_schedule_run(manual["run_id"])["status"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
