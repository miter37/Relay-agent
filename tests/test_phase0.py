from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.api import list_runs, run_artifacts, run_detail, run_events, run_result
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.validation import reconcile_json_artifacts, scan_artifacts


class Phase0Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_new_job_records_run_metadata_and_snapshot(self):
        job, reused = self.engine.create_job(
            JobRequest(task="Inspect the existing file", worker="codex"), queued=True, submitted_via="gui"
        )
        self.assertFalse(reused)
        self.assertTrue(job["job_id"])
        self.assertEqual(job["trigger_type"], "manual")
        self.assertIsNone(job["task_id"])
        snapshot = json.loads(job["task_snapshot_json"])
        self.assertEqual(snapshot["task"], "Inspect the existing file")
        self.assertEqual(snapshot["worker"], "codex")
        self.assertEqual(snapshot["trigger_type"], "manual")

    def test_rerun_records_rerun_trigger(self):
        job, _ = self.engine.create_job(JobRequest(task="Original", worker="codex"), queued=True, submitted_via="gui")
        self.db.update_job(
            job["job_id"],
            status="FAILED",
            request_json=json.dumps(JobRequest(task="Original", worker="codex").to_dict()),
        )
        result = self.engine.queue_rerun(job["job_id"])
        rerun = self.db.get_job(result["job_id"])
        self.assertEqual(rerun["trigger_type"], "rerun")

    def test_artifact_records_have_external_identity_and_producer(self):
        job, _ = self.engine.create_job(JobRequest(task="Record artifact", worker="codex"), queued=True)
        attempt_id = self.db.create_attempt(job["job_id"], "codex")
        self.db.add_artifact(
            job["job_id"],
            relative_path="result.txt",
            final_path="result.txt",
            mime_type="text/plain",
            size=1,
            sha256="hash",
            artifact_uid="artifact-uid",
            role="output",
            producer_attempt_id=attempt_id,
        )
        artifact = self.db.artifacts_for_job(job["job_id"])[0]
        self.assertEqual(artifact["artifact_uid"], "artifact-uid")
        self.assertEqual(artifact["role"], "output")
        self.assertEqual(artifact["producer_attempt_id"], attempt_id)

    def test_declared_artifact_role_survives_scan_and_result_reconciliation(self):
        artifact_dir = Path(self.temp.name) / "artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "composition.json").write_text("{}", encoding="utf-8")
        records = scan_artifacts(artifact_dir, 10, 1024, {"composition.json": "composition"})
        self.assertEqual(records[0]["role"], "composition")
        value = {"artifacts": [{"relative_path": "composition.json", "description": "handoff", "role": "composition"}]}
        reconciled = reconcile_json_artifacts(value, records)
        self.assertEqual(reconciled["artifacts"][0]["role"], "composition")

    def test_run_aliases_preserve_job_id(self):
        job, _ = self.engine.create_job(JobRequest(task="Alias", worker="codex"), queued=True)
        detail = run_detail(self.engine, job["job_id"])
        self.assertEqual(detail["run_id"], job["job_id"])
        self.assertEqual(run_result(self.db, job["job_id"])["run_id"], job["job_id"])
        self.assertEqual(run_artifacts(self.db, job["job_id"])["run_id"], job["job_id"])
        self.assertEqual(run_events(self.db, job["job_id"])["run_id"], job["job_id"])
        listed = list_runs(self.db)
        self.assertEqual(listed["runs"][0]["run_id"], job["job_id"])


if __name__ == "__main__":
    unittest.main()
