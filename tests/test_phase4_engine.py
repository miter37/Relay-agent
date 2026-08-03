from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec


class Phase4EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _create_task(self, **kwargs) -> dict:
        spec = TaskSpec(name=kwargs.pop("name", "T"), instructions=kwargs.pop("instructions", "run"))
        spec.fallback_enabled = kwargs.pop("fallback_enabled", True)
        spec.default_worker = kwargs.pop("default_worker", "codex")
        spec.profile = kwargs.pop("profile", "web-research")
        spec.result_format = kwargs.pop("result_format", "json")
        spec.timeout_seconds = kwargs.pop("timeout_seconds", None)
        return self.engine.create_task(spec)

    def test_load_task_for_snapshot_returns_full_definition(self):
        task = self._create_task(name="HBM report", instructions="summarize HBM supply")
        snap = self.engine.load_task_for_snapshot(task["task_id"])
        for key in ("task_id", "name", "version", "instructions", "default_worker", "fallback_enabled",
                     "timeout_seconds", "profile", "result_format"):
            self.assertIn(key, snap)
        self.assertEqual(snap["name"], "HBM report")
        self.assertEqual(snap["instructions"], "summarize HBM supply")

    def test_run_task_from_snapshot_pins_version_and_overrides(self):
        task = self._create_task(name="Weekly", instructions="original")
        snap = self.engine.load_task_for_snapshot(task["task_id"])
        # Mutate the live Task after the snapshot is taken.
        self.engine.update_task(task["task_id"], instructions="modified")
        job, reused = self.engine.run_task_from_snapshot(snap, queued=True, submitted_via="cli")
        self.assertFalse(reused)
        snapshot = __import__("json").loads(job["task_snapshot_json"])
        self.assertEqual(snapshot["task_definition"]["instructions"], "original")
        self.assertEqual(snapshot["task_version"], snap["version"])

    def test_run_task_from_snapshot_with_override_applies_worker(self):
        task = self._create_task(default_worker="codex")
        snap = self.engine.load_task_for_snapshot(task["task_id"])
        request = JobRequest(task=snap["instructions"], worker="claude")
        job, reused = self.engine.run_task_from_snapshot(
            snap,
            request=request,
            queued=True,
            submitted_via="cli",
        )
        self.assertEqual(job["requested_worker"], "claude")
        self.assertFalse(reused)


if __name__ == "__main__":
    unittest.main()
