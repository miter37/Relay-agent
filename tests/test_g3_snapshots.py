from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from relay.agent_registry import AgentRegistry
from relay.config import Config
from relay.errors import RelayError
from relay.models import JobRequest
from relay.schedules.snapshots import (
    build_scheduled_request,
    materialize_snapshot,
    schedule_output_paths,
    validate_source_job,
)


class G3SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.config = Config(self.home)
        self.config.init()
        self.registry = AgentRegistry(self.config, self.config.path_value("adapter_spec_root"))
        self.attachment = self.home / "input" / "report.txt"
        self.attachment.parent.mkdir(parents=True, exist_ok=True)
        self.attachment.write_text("source attachment", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def completed_job(self, **overrides):
        request = {
            "task": "Build a scheduled report",
            "worker": "codex",
            "profile": "web-research",
            "result_format": "json",
            "attachments": [str(self.attachment)],
        }
        job = {
            "job_id": "source-job",
            "status": "COMPLETED",
            "result_status": "complete",
            "replayable": 1,
            "request_json": json.dumps(request),
            "task_text": request["task"],
            "actual_worker": "codex",
        }
        job.update(overrides)
        return job

    def test_completed_replayable_job_is_eligible(self):
        request = validate_source_job(self.completed_job(), self.registry)

        self.assertEqual(request.task, "Build a scheduled report")
        self.assertEqual(request.worker, "codex")
        self.assertEqual(request.attachments, [str(self.attachment)])

    def test_target_writing_job_is_not_schedule_eligible(self):
        job = self.completed_job()
        request = json.loads(job["request_json"])
        request["target_path"] = str(Path(self.temp.name) / "project")
        job["request_json"] = json.dumps(request)

        with self.assertRaises(RelayError) as context:
            validate_source_job(job, self.registry)

        self.assertEqual(context.exception.code, "SCHEDULE_TARGET_UNSUPPORTED")

    def test_partial_or_non_replayable_job_is_rejected(self):
        for overrides, code in (
            ({"status": "PARTIAL", "result_status": "partial"}, "SCHEDULE_NOT_ELIGIBLE"),
            ({"replayable": 0}, "SCHEDULE_NOT_ELIGIBLE"),
            ({"request_json": "{}"}, "SCHEDULE_INPUT_MISSING"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(RelayError) as context:
                    validate_source_job(self.completed_job(**overrides), self.registry)
                self.assertEqual(context.exception.code, code)

    def test_snapshot_materializes_task_and_attachment_manifest(self):
        snapshot = materialize_snapshot(
            self.config,
            "schedule-1",
            "Build a scheduled report",
            [str(self.attachment)],
        )

        self.assertTrue(snapshot.task_file.is_file())
        self.assertEqual(snapshot.task_file.read_text(encoding="utf-8"), "Build a scheduled report")
        self.assertEqual(len(snapshot.attachments), 1)
        self.assertTrue(snapshot.attachments[0].is_file())
        manifest = json.loads(snapshot.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["attachments"][0]["size"], len("source attachment"))

    def test_snapshot_rejects_duplicate_names_and_missing_attachment(self):
        second = self.home / "other" / self.attachment.name
        second.parent.mkdir(parents=True)
        second.write_text("another", encoding="utf-8")
        with self.assertRaises(RelayError) as duplicate:
            materialize_snapshot(self.config, "schedule-duplicate", "Task", [str(self.attachment), str(second)])
        self.assertEqual(duplicate.exception.code, "SCHEDULE_INPUT_INVALID")

        with self.assertRaises(RelayError) as missing:
            materialize_snapshot(self.config, "schedule-missing", "Task", [str(self.home / "missing.txt")])
        self.assertEqual(missing.exception.code, "SCHEDULE_INPUT_MISSING")

    def test_failed_snapshot_does_not_leave_final_directory(self):
        with self.assertRaises(RelayError):
            materialize_snapshot(self.config, "schedule-failed", "Task", [str(self.home / "missing.txt")])

        self.assertFalse((self.home / "schedule-inputs" / "schedule-failed").exists())

    def test_scheduled_request_uses_snapshot_and_unique_output_paths(self):
        source = JobRequest(task="Task", worker="codex", attachments=[str(self.attachment)], model="test-model")
        snapshot = materialize_snapshot(self.config, "schedule-1", source.task, source.attachments)
        output, artifacts = schedule_output_paths(
            self.config,
            "schedule-1",
            "run-abc123",
            datetime.fromisoformat("2026-07-24T09:00:00+09:00"),
            "json",
        )

        request = build_scheduled_request(source, snapshot, output, artifacts)

        self.assertEqual(request.caller, "schedule")
        self.assertTrue(request.force_new)
        self.assertEqual(request.task_file, str(snapshot.task_file))
        self.assertEqual(request.attachments, [str(snapshot.attachments[0])])
        self.assertEqual(request.output_path, str(output))
        self.assertEqual(request.artifact_path, str(artifacts))
        self.assertIsNone(request.request_id)
        self.assertNotEqual(request.workspace, str(self.home / "outside"))


if __name__ == "__main__":
    unittest.main()
