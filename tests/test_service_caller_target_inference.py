"""Real bug found via live Orchestrator usage (2026-08-10): create_job() ran
infer_target_path() on task text before checking whether the caller is even allowed to
use working-folder mode. The Orchestrator's own prompt embeds raw worker log/error text
(which can contain filesystem paths) alongside words like "write", so a legitimate
service-to-service reasoning call could spuriously fail with TARGET_PATH_NOT_ALLOWED (or
TARGET_PATH_AMBIGUOUS on multiple paths) even though it never asked for a working folder.
Service-type callers can never use target_path at all, so inference should be skipped
for them entirely.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest


class ServiceCallerTargetInferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_service_caller_with_write_intent_and_path_text_does_not_raise(self):
        # Mirrors what an Orchestrator prompt actually contains: a "write" instruction
        # plus a raw filesystem path pulled from embedded worker log output.
        task_text = (
            "Write a short closing report. Recent log tail:\n"
            r"C:\Users\doyoon.kim\AppData\Local\Relay\workspace\antigravity\01ABC\runtime\stderr.log"
        )
        job, _reused = self.engine.create_job(
            JobRequest(task=task_text, caller="service", worker="auto"),
            queued=True,
            submitted_via="orchestrator",
        )
        self.assertIsNotNone(job["job_id"])

    def test_service_caller_with_multiple_paths_does_not_raise_ambiguous(self):
        task_text = (
            "Write and update these paths: "
            r"C:\Users\a\one" + " and " + r"C:\Users\a\two"
        )
        job, _reused = self.engine.create_job(
            JobRequest(task=task_text, caller="service", worker="auto"),
            queued=True,
            submitted_via="orchestrator",
        )
        self.assertIsNotNone(job["job_id"])

    def test_explicit_target_path_from_service_caller_is_still_rejected(self):
        """The inference skip must not weaken the existing, deliberate restriction: a
        service caller explicitly asking for a working folder is still refused."""
        from relay.errors import RelayError

        with tempfile.TemporaryDirectory() as target_dir:
            with self.assertRaises(RelayError) as ctx:
                self.engine.create_job(
                    JobRequest(task="do something", caller="service", worker="auto", target_path=target_dir),
                    queued=True,
                    submitted_via="orchestrator",
                )
            self.assertEqual(ctx.exception.code, "TARGET_PATH_NOT_ALLOWED")

    def test_cli_caller_still_gets_target_inference(self):
        """Interactive callers keep the existing behavior unchanged."""
        import json

        with tempfile.TemporaryDirectory() as workdir:
            task_text = f'Please write changes to "{workdir}"'
            job, _reused = self.engine.create_job(
                JobRequest(task=task_text, caller="human", worker="auto"),
                queued=True,
                submitted_via="cli",
            )
            stored = json.loads(self.db.get_job(job["job_id"])["request_json"])
            self.assertIsNotNone(stored.get("target_path"))


if __name__ == "__main__":
    unittest.main()
