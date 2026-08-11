from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.quality.service import QualityService


class Phase6cQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.quality_service = QualityService(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_score_completed_run_without_uncertainties_is_high(self):
        job, _ = self.engine.create_job(JobRequest(task="Task 1"), queued=True)
        self.db.update_job(job["job_id"], status="COMPLETED", result_status="complete")
        self.db.add_artifact(
            job["job_id"],
            relative_path="out.txt",
            final_path=str(self.home / "out.txt"),
            mime_type="text/plain",
            size=10,
            sha256="abc",
            artifact_uid="art-q1",
            role="output",
        )

        res = self.quality_service.score_run(job["job_id"])
        self.assertEqual(res["score"], "high")
        self.assertTrue(res["status_ok"])

    def test_score_failed_run_is_low(self):
        job, _ = self.engine.create_job(JobRequest(task="Task 2"), queued=True)
        self.db.update_job(job["job_id"], status="FAILED", error_code="WORKER_FAILED")

        res = self.quality_service.score_run(job["job_id"])
        self.assertEqual(res["score"], "low")
        self.assertFalse(res["status_ok"])

    def test_many_uncertainties_are_low_quality(self):
        job, _ = self.engine.create_job(JobRequest(task="Uncertain"), queued=True)
        output = Path(job["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            __import__("json").dumps({"uncertainties": [f"u{i}" for i in range(10)], "missing_items": []}),
            encoding="utf-8",
        )
        self.db.update_job(job["job_id"], status="COMPLETED", result_status="complete")
        self.db.add_artifact(
            job["job_id"],
            relative_path="out.json",
            final_path=str(output),
            mime_type="application/json",
            size=output.stat().st_size,
            sha256="not-used",
            artifact_uid="art-many-uncertainties",
            role="output",
        )

        result = self.quality_service.score_run(job["job_id"])

        self.assertEqual(result["score"], "low")

    def test_attention_runs_filters_by_low_quality(self):
        job1, _ = self.engine.create_job(JobRequest(task="Task High"), queued=True)
        self.db.update_job(job1["job_id"], status="COMPLETED", result_status="complete")
        self.db.add_artifact(
            job1["job_id"],
            relative_path="out.txt",
            final_path=str(self.home / "out.txt"),
            mime_type="text/plain",
            size=10,
            sha256="abc",
            artifact_uid="art-q2",
            role="output",
        )

        job2, _ = self.engine.create_job(JobRequest(task="Task Low"), queued=True)
        self.db.update_job(job2["job_id"], status="FAILED", error_code="ALL_WORKERS_FAILED")

        items = self.quality_service.attention_runs(status_filter="low")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["run_id"], job2["job_id"])

    def test_attention_runs_includes_failed_project_runs(self):
        self.db.create_project(
            {
                "project_id": "quality-project",
                "name": "Quality Project",
                "description": None,
                "version": 1,
                "definition_json": "{}",
            }
        )
        self.db.create_project_run(
            {
                "project_run_id": "quality-project-run",
                "project_id": "quality-project",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "failed",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )

        items = self.quality_service.attention_runs(status_filter="low")

        self.assertIn("quality-project-run", {item["run_id"] for item in items})


if __name__ == "__main__":
    unittest.main()
