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
from relay.lifecycle.import_service import ImportService
from relay.models import JobRequest, TaskSpec


class Phase6eImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home1 = Path(self.temp.name) / "home1"
        self.config1 = Config(self.home1)
        self.config1.init()
        self.db1 = Database(self.config1.path_value("database_path"))
        self.engine1 = RelayEngine(self.config1, self.db1)

        self.home2 = Path(self.temp.name) / "home2"
        self.config2 = Config(self.home2)
        self.config2.init()
        self.db2 = Database(self.config2.path_value("database_path"))

        self.export_service = ExportService(self.db1, self.config1)
        self.import_service = ImportService(self.db2, self.config2)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_round_trip(self):
        # Create task in db1
        self.engine1.create_task(TaskSpec(name="Importable Task", instructions="inst", task_id="t-imp-1"))

        # Export from db1
        zip_path = Path(self.temp.name) / "export.zip"
        self.export_service.export(out_path=zip_path)

        # Import into db2
        res = self.import_service.import_archive(zip_path, conflict="skip")
        self.assertTrue(res["ok"])
        self.assertEqual(res["imported_tasks"], 1)

        # Verify task exists in db2
        task2 = self.db2.get_task("t-imp-1")
        self.assertIsNotNone(task2)
        self.assertEqual(task2["name"], "Importable Task")

    def test_import_conflict_resolution(self):
        # Create task in db1 and export
        self.engine1.create_task(TaskSpec(name="Conflict Task", instructions="v1", task_id="t-conf-1"))
        zip_path = Path(self.temp.name) / "export.zip"
        self.export_service.export(out_path=zip_path)

        # Pre-create task with same name in db2
        engine2 = RelayEngine(self.config2, self.db2)
        engine2.create_task(TaskSpec(name="Conflict Task", instructions="v2", task_id="t-conf-2"))

        # Import with conflict=skip
        res_skip = self.import_service.import_archive(zip_path, conflict="skip")
        self.assertEqual(res_skip["imported_tasks"], 0)
        self.assertEqual(res_skip["conflicts"], 1)

    def test_include_runs_restores_job_artifact_and_lineage_metadata(self):
        job, _ = self.engine1.create_job(JobRequest(task="archived run"), queued=True)
        artifact_dir = self.config1.path_value("artifact_root") / job["job_id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_file = artifact_dir / "report.md"
        artifact_file.write_text("archive payload", encoding="utf-8")
        self.db1.add_artifact(
            job["job_id"],
            relative_path="report.md",
            final_path=str(artifact_file),
            mime_type="text/markdown",
            size=artifact_file.stat().st_size,
            sha256=hashlib.sha256(artifact_file.read_bytes()).hexdigest(),
            artifact_uid="archive-artifact-1",
            role="report",
        )
        zip_path = Path(self.temp.name) / "runs.zip"
        self.export_service.export(include_runs=True, out_path=zip_path)

        result = self.import_service.import_archive(zip_path, include_runs=True)

        self.assertEqual(result["imported_runs"], 1)
        restored_job = self.db2.get_job(job["job_id"])
        self.assertIsNotNone(restored_job)
        restored_artifact = self.db2.artifact_by_uid("archive-artifact-1")
        self.assertEqual(Path(restored_artifact["final_path"]).read_text(encoding="utf-8"), "archive payload")

    def test_import_rejects_manifest_hash_mismatch(self):
        self.engine1.create_task(TaskSpec(name="Integrity", instructions="check", task_id="integrity-task"))
        original = Path(self.temp.name) / "original.zip"
        tampered = Path(self.temp.name) / "tampered.zip"
        self.export_service.export(out_path=original)
        with zipfile.ZipFile(original, "r") as src, zipfile.ZipFile(tampered, "w") as dest:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename == "tasks/integrity-task.json":
                    data = b"{}"
                dest.writestr(info, data)

        with self.assertRaisesRegex(Exception, "hash"):
            self.import_service.import_archive(tampered)

    def test_import_rejects_artifact_path_escape(self):
        job, _ = self.engine1.create_job(JobRequest(task="escape"), queued=True)
        artifact_dir = self.config1.path_value("artifact_root") / job["job_id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_file = artifact_dir / "safe.txt"
        artifact_file.write_text("safe", encoding="utf-8")
        self.db1.add_artifact(
            job["job_id"],
            relative_path="safe.txt",
            final_path=str(artifact_file),
            mime_type="text/plain",
            size=4,
            sha256=hashlib.sha256(b"safe").hexdigest(),
            artifact_uid="escape-artifact",
            role="output",
        )
        original = Path(self.temp.name) / "safe.zip"
        malicious = Path(self.temp.name) / "malicious.zip"
        self.export_service.export(include_runs=True, out_path=original)
        with zipfile.ZipFile(original, "r") as src:
            entries = {name: src.read(name) for name in src.namelist()}
        meta_name = "artifacts/escape-artifact.meta.json"
        meta = json.loads(entries[meta_name])
        meta["relative_path"] = "../../escaped.txt"
        entries[meta_name] = json.dumps(meta).encode("utf-8")
        manifest = json.loads(entries["manifest.json"])
        manifest["sha256"][meta_name] = hashlib.sha256(entries[meta_name]).hexdigest()
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")
        with zipfile.ZipFile(malicious, "w") as dest:
            for name, data in entries.items():
                dest.writestr(name, data)

        with self.assertRaisesRegex(Exception, "outside artifact root"):
            self.import_service.import_archive(malicious, include_runs=True)

    def test_rename_conflict_rewrites_project_and_routine_references(self):
        task = self.engine1.create_task(TaskSpec(name="Shared Task", instructions="source", task_id="source-task"))
        project = self.engine1.project_service.create_project(
            {
                "name": "Shared Project",
                "nodes": [{"node_id": "n1", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        from relay.routines.service import RoutineService

        RoutineService(self.config1, self.db1, self.engine1).create_routine(
            {
                "name": "Shared Routine",
                "target_type": "project",
                "target_id": project["project_id"],
                "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"},
                "timezone": "UTC",
            }
        )
        existing = RelayEngine(self.config2, self.db2)
        existing.create_task(TaskSpec(name="Shared Task", instructions="destination", task_id="existing-task"))

        archive = Path(self.temp.name) / "references.zip"
        self.export_service.export(out_path=archive)
        result = self.import_service.import_archive(archive, conflict="rename")

        self.assertEqual(result["imported_tasks"], 1)
        imported_tasks = [t for t in self.db2.list_tasks() if t["name"].startswith("Imported Shared Task")]
        self.assertEqual(len(imported_tasks), 1)
        imported_project = next(p for p in self.db2.list_projects() if p["name"] == "Shared Project")
        definition = json.loads(imported_project["definition_json"])
        self.assertEqual(definition["nodes"][0]["task_id"], imported_tasks[0]["task_id"])
        imported_routine = self.db2.list_routines()[0]
        self.assertEqual(imported_routine["target_id"], imported_project["project_id"])


if __name__ == "__main__":
    unittest.main()
