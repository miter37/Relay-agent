from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec
from relay.projects.service import ProjectService
from relay.reviews.service import ReviewService


class ReviewGateFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _candidate_job(self):
        job, _ = self.engine.create_job(
            JobRequest(task="produce a result", review_mode="human", force_new=True),
            submitted_via="gui",
        )
        output = Path(job["output_path"])
        artifact_root = Path(job["artifact_path"])
        candidate_root = self.home / "review-candidates" / job["job_id"]
        (candidate_root / "artifacts").mkdir(parents=True)
        candidate_output = candidate_root / output.name
        candidate_output.write_text("candidate result", encoding="utf-8")
        (candidate_root / "artifacts" / "notes.txt").write_text("candidate notes", encoding="utf-8")
        self.db.update_job(
            job["job_id"],
            status="COMPLETED",
            result_status="complete",
            review_status="pending_human",
            review_candidate_root=str(candidate_root),
            receipt_json=json.dumps(
                {"result_path": str(candidate_output), "artifact_path": str(candidate_root / "artifacts")}
            ),
        )
        self.db.add_artifact(
            job["job_id"],
            relative_path=output.name,
            final_path=str(candidate_output),
            mime_type="text/plain",
            size=candidate_output.stat().st_size,
            sha256="candidate",
            artifact_uid="artifact-result",
            role="result",
            publication_status="candidate",
        )
        self.db.add_artifact(
            job["job_id"],
            relative_path="notes.txt",
            final_path=str(candidate_root / "artifacts" / "notes.txt"),
            mime_type="text/plain",
            size=14,
            sha256="candidate",
            artifact_uid="artifact-notes",
            role="output",
            publication_status="candidate",
        )
        return self.db.get_job(job["job_id"]), output, artifact_root

    def test_candidate_is_hidden_until_confirmed(self):
        job, output, artifact_root = self._candidate_job()
        service = ReviewService(self.db, self.engine, self.config)
        review = service.create_task_review(job["job_id"])
        self.assertEqual(review["review"]["status"], "pending_human")
        self.assertFalse(output.exists())
        self.assertEqual(self.db.artifact_by_uid("artifact-result")["publication_status"], "candidate")

        confirmed = service.confirm(review["review"]["review_id"])
        self.assertEqual(confirmed["review"]["status"], "approved")
        self.assertTrue(output.is_file())
        self.assertTrue((artifact_root / "notes.txt").is_file())
        self.assertEqual(self.db.artifact_by_uid("artifact-result")["final_path"], str(output))
        self.assertEqual(self.db.artifact_by_uid("artifact-result")["publication_status"], "published")

    def test_rejection_keeps_candidate_unpublished(self):
        job, output, _artifact_root = self._candidate_job()
        service = ReviewService(self.db, self.engine, self.config)
        review = service.create_task_review(job["job_id"])
        result = service.reject(review["review"]["review_id"], "Missing source citation")
        self.assertEqual(result["review"]["status"], "rejected")
        self.assertFalse(output.exists())
        self.assertEqual(self.db.artifact_by_uid("artifact-result")["publication_status"], "rejected")

    def test_review_columns_are_available_on_current_schema(self):
        with self.db.connect() as conn:
            job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            self.assertTrue({"review_status", "review_id", "review_policy_json"} <= job_columns)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"review_sessions", "review_rounds"} <= tables)

    def test_orchestrator_review_uses_project_supervisor_config(self):
        task = self.engine.create_task(TaskSpec(name="reviewed project task", instructions="produce evidence"))
        project = ProjectService(self.db, self.engine).create_project(
            {
                "name": "Automatic review",
                "nodes": [
                    {
                        "node_id": "research",
                        "task_id": task["task_id"],
                        "checkpoint": {
                            "enabled": True,
                            "reviewer": "orchestrator",
                            "guidelines": "Check the evidence.",
                            "max_reruns": 1,
                        },
                    }
                ],
                "connections": [],
                "output_selection": [],
                "orchestrator": {"enabled": True, "worker": "codex", "model": "review-model"},
            }
        )
        project_run = ProjectService(self.db, self.engine).create_project_run(project["project_id"])
        job, _ = self.engine.create_job(JobRequest(task="produce evidence", force_new=True), submitted_via="project")
        output = Path(job["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"answer":"evidence"}', encoding="utf-8")
        self.db.update_job(
            job["job_id"],
            status="COMPLETED",
            result_status="complete",
            receipt_json=json.dumps({"ok": True}),
        )
        self.db.add_artifact(
            job["job_id"],
            relative_path=output.name,
            final_path=str(output),
            mime_type="application/json",
            size=output.stat().st_size,
            sha256="evidence",
            artifact_uid="project-review-result",
            role="result",
        )
        self.db.update_project_step(project_run["project_run_id"], "research", active_task_run_id=job["job_id"])

        service = ReviewService(self.db, self.engine, self.config)
        review = service.create_project_review(
            project_run["project_run_id"],
            "research",
            job["job_id"],
            {
                "enabled": True,
                "reviewer": "orchestrator",
                "guidelines": "Check the evidence.",
                "max_reruns": 1,
            },
        )
        with patch("relay.orchestrator.agent.OrchestratorAgent") as agent_cls:
            agent_cls.return_value.review.return_value = {
                "decision": "approve",
                "reason": "Evidence is sufficient.",
                "comment": "",
            }
            result = service.evaluate_orchestrator(review["review"]["review_id"])

        self.assertEqual(result["review"]["status"], "approved")
        agent_cls.assert_called_once_with(self.engine, worker="codex", model="review-model", profile=None)
        agent_cls.return_value.review.assert_called_once()


if __name__ == "__main__":
    unittest.main()
