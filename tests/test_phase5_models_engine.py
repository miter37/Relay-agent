from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import TaskSpec, JobRequest
from relay.routines.models import RoutineSpec
from relay.schedules.rules import next_occurrences, Occurrence


class RoutineSpecTests(unittest.TestCase):
    def test_valid_task_target_routine(self):
        def task_lookup(tid):
            return {"name": "T", "version": 1} if tid == "T-1" else None

        spec = RoutineSpec.from_dict({
            "name": "Daily HBM",
            "target_type": "task",
            "target_id": "T-1",
            "rule": {"type": "daily", "times": ["09:00"]},
            "timezone": "Asia/Seoul",
        })
        spec.validate(task_lookup=task_lookup, project_lookup=lambda _p: None)
        self.assertEqual(spec.target_type, "task")

    def test_invalid_target_type_rejected(self):
        def task_lookup(_t): return {"name": "T", "version": 1}
        spec = RoutineSpec.from_dict({
            "name": "X", "target_type": "garbage", "target_id": "T-1",
            "rule": {"type": "daily", "times": ["09:00"]}, "timezone": "Asia/Seoul",
        })
        with self.assertRaisesRegex(RelayError, "ROUTINE_INVALID"):
            spec.validate(task_lookup=task_lookup, project_lookup=lambda _p: None)

    def test_missing_task_target_rejected(self):
        spec = RoutineSpec.from_dict({
            "name": "X", "target_type": "task", "target_id": "no-such",
            "rule": {"type": "daily", "times": ["09:00"]}, "timezone": "Asia/Seoul",
        })
        with self.assertRaisesRegex(RelayError, "ROUTINE_TARGET_MISSING"):
            spec.validate(task_lookup=lambda _t: None, project_lookup=lambda _p: None)

    def test_pinned_requires_pinned_version(self):
        def task_lookup(_t): return {"name": "T", "version": 1}
        # pinned without version
        spec = RoutineSpec.from_dict({
            "name": "X", "target_type": "task", "target_id": "T-1",
            "rule": {"type": "daily", "times": ["09:00"]}, "timezone": "Asia/Seoul",
            "version_policy": "pinned", "pinned_version": 1,
        })
        # Also missing pinned_version
        spec2 = RoutineSpec.from_dict({
            "name": "X", "target_type": "task", "target_id": "T-1",
            "rule": {"type": "daily", "times": ["09:00"]}, "timezone": "Asia/Seoul",
            "version_policy": "pinned",
        })
        with self.assertRaisesRegex(RelayError, "ROUTINE_INVALID"):
            spec2.validate(task_lookup=task_lookup, project_lookup=lambda _p: None)


class EnginePassthroughTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.task = self.engine.create_task(TaskSpec(name="HBM", instructions="run"))

    def tearDown(self):
        self.temp.cleanup()

    def test_run_task_records_routine_id(self):
        job, reused, _ = self.engine.run_task(
            self.task["task_id"],
            queued=True,
            submitted_via="routine",
            trigger_type="routine",
            routine_id="r-1",
        )
        self.assertFalse(reused)
        self.assertEqual(job["trigger_type"], "routine")
        self.assertEqual(job.get("routine_id"), "r-1")
        self.assertEqual(self.db.get_job(job["job_id"])["routine_id"], "r-1")
