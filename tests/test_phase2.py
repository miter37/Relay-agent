from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from relay.api import artifact_content, search_artifacts, search_runs
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_run_and_artifact_search_are_rebuildable(self):
        job, _ = self.engine.create_job(
            JobRequest(task="Semiconductor HBM supply report", title="Weekly HBM report", worker="codex"),
            queued=True,
        )
        self.db.update_job(job["job_id"], status="COMPLETED", result_status="complete")
        self.db.index_run(job["job_id"])
        output = self.config.path_value("artifact_root") / job["job_id"] / "report.md"
        output.parent.mkdir(parents=True)
        output.write_text("HBM supply shortage and pricing", encoding="utf-8")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        self.db.add_artifact(
            job["job_id"],
            relative_path="report.md",
            final_path=str(output),
            mime_type="text/markdown",
            size=output.stat().st_size,
            sha256=digest,
            artifact_uid="phase2-artifact",
            role="output",
        )
        self.assertTrue(self.db.rebuild_search_index())
        runs = search_runs(self.db, query="HBM supply", status="completed")
        self.assertEqual(runs["items"][0]["run_id"], job["job_id"])
        artifacts = search_artifacts(self.db, query="shortage", role="output")
        self.assertEqual(artifacts["items"][0]["artifact_uid"], "phase2-artifact")
        self.assertTrue(self.db.rebuild_search_index())
        self.assertEqual(search_artifacts(self.db, query="shortage")["items"][0]["artifact_uid"], "phase2-artifact")

    def test_artifact_content_is_uid_bound_and_limited(self):
        job, _ = self.engine.create_job(JobRequest(task="Content", worker="codex"), queued=True)
        path = self.config.path_value("artifact_root") / job["job_id"] / "content.txt"
        path.parent.mkdir(parents=True)
        path.write_text("0123456789", encoding="utf-8")
        self.db.add_artifact(
            job["job_id"],
            relative_path="content.txt",
            final_path=str(path),
            mime_type="text/plain",
            size=10,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            artifact_uid="content-artifact",
            role="output",
        )
        result = artifact_content(self.db, "content-artifact", max_bytes=4)
        self.assertEqual(result["text"], "0123")
        self.assertTrue(result["truncated"])

    def test_artifact_content_reads_previewable_yaml_with_replacement(self):
        job, _ = self.engine.create_job(JobRequest(task="YAML preview", worker="codex"), queued=True)
        path = self.config.path_value("artifact_root") / job["job_id"] / "report.yaml"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"title: Relay\ninvalid: \xff")
        self.db.add_artifact(
            job["job_id"],
            relative_path="report.yaml",
            final_path=str(path),
            mime_type="application/yaml",
            size=path.stat().st_size,
            sha256="yaml",
            artifact_uid="yaml-artifact",
        )
        payload = artifact_content(self.db, "yaml-artifact", max_bytes=262144)
        self.assertTrue(payload["available"])
        self.assertIn("title: Relay", payload["text"])

    def test_non_replayable_task_is_not_recovered_by_search(self):
        job, _ = self.engine.create_job(JobRequest(task="Private phrase", worker="codex"), queued=True)
        self.db.update_job(job["job_id"], replayable=0, task_text=None, task_preview=None, request_json="{}")
        self.db.rebuild_search_index()
        self.assertEqual(search_runs(self.db, query="Private phrase")["items"], [])

    def test_reopening_database_backfills_stale_search_index(self):
        job, _ = self.engine.create_job(
            JobRequest(task="Backfill search phrase", title="Backfill test", worker="codex"), queued=True
        )
        self.db.update_job(job["job_id"], status="COMPLETED", result_status="complete")

        reopened = Database(self.db.path)
        runs = search_runs(reopened, query="Backfill phrase")
        self.assertEqual(runs["items"][0]["run_id"], job["job_id"])


if __name__ == "__main__":
    unittest.main()
