from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.lifecycle.export_service import ExportService
from relay.models import TaskSpec
from relay.routines.service import RoutineService


class Phase6eExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.export_service = ExportService(self.db, self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_produces_valid_deterministic_zip(self):
        # Create a task
        self.engine.create_task(TaskSpec(name="T1", instructions="inst1", task_id="task-export-1"))

        out_zip = Path(self.temp.name) / "export1.zip"
        archive_path = self.export_service.export(out_path=out_zip)
        self.assertTrue(archive_path.is_file())

        # Inspect zip entries
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("tasks/task-export-1.json", names)

            # Check manifest content
            manifest = __import__("json").loads(zf.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["export_schema_version"], 1)
            self.assertIn("tasks/task-export-1.json", manifest["sha256"])

        # Determinism check: second export produces exact same bytes
        out_zip2 = Path(self.temp.name) / "export2.zip"
        self.export_service.export(out_path=out_zip2)
        self.assertEqual(out_zip.read_bytes(), out_zip2.read_bytes())

    def test_export_redacts_notification_secrets(self):
        task = self.engine.create_task(TaskSpec(name="Target", instructions="run"))
        RoutineService(self.config, self.db, self.engine).create_routine(
            {
                "name": "Secret routine",
                "target_type": "task",
                "target_id": task["task_id"],
                "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"},
                "timezone": "UTC",
                "notification_policy": {
                    "on_failure": [{"kind": "webhook", "url": "http://localhost/hook", "secret": "do-not-export"}]
                },
            }
        )
        archive = self.export_service.export(out_path=Path(self.temp.name) / "redacted.zip")

        self.assertNotIn(b"do-not-export", archive.read_bytes())
        with zipfile.ZipFile(archive) as zf:
            routine_name = next(name for name in zf.namelist() if name.startswith("routines/"))
            routine = json.loads(zf.read(routine_name))
            self.assertNotIn("secret", routine["notification_policy_json"])

    def test_export_rejects_missing_artifact_file(self):
        job, _ = self.engine.create_job(__import__("relay.models", fromlist=["JobRequest"]).JobRequest(task="missing"))
        self.db.add_artifact(
            job["job_id"],
            relative_path="missing.txt",
            final_path=str(self.config.path_value("artifact_root") / job["job_id"] / "missing.txt"),
            mime_type="text/plain",
            size=3,
            sha256=hashlib.sha256(b"out").hexdigest(),
            artifact_uid="missing-export-artifact",
            role="output",
        )

        with self.assertRaisesRegex(Exception, "EXPORT_FAILED"):
            self.export_service.export(include_runs=True, out_path=Path(self.temp.name) / "missing.zip")

    def test_export_artifact_metadata_does_not_leak_local_absolute_path(self):
        job, _ = self.engine.create_job(__import__("relay.models", fromlist=["JobRequest"]).JobRequest(task="path"))
        artifact_dir = self.config.path_value("artifact_root") / job["job_id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_file = artifact_dir / "path.txt"
        artifact_file.write_text("path", encoding="utf-8")
        self.db.add_artifact(
            job["job_id"],
            relative_path="path.txt",
            final_path=str(artifact_file),
            mime_type="text/plain",
            size=4,
            sha256=hashlib.sha256(b"path").hexdigest(),
            artifact_uid="path-artifact",
            role="output",
        )

        archive = self.export_service.export(include_runs=True, out_path=Path(self.temp.name) / "paths.zip")

        with zipfile.ZipFile(archive) as zf:
            metadata = zf.read("artifacts/path-artifact.meta.json")
        self.assertNotIn(str(self.home).encode(), metadata)


if __name__ == "__main__":
    unittest.main()
