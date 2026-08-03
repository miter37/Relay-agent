from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import TaskSpec
from relay.projects.models import (
    ProjectNode,
    ProjectOutputSelection,
    ProjectSpec,
)
from relay.projects.service import ProjectService
from relay.approvals.service import ApprovalService


class Phase6aServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.config = Config(self.home)
        self.config.init()
        self.allow_dir = self.root / "deliveries"
        self.allow_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.project_service = ProjectService(self.db, self.engine)
        self.approval_service = ApprovalService(self.db, self.engine, self.config)

    def tearDown(self):
        self.temp.cleanup()

    def _setup_project_run_at_checkpoint(self, checkpoint_node="review"):
        t1 = self.engine.create_task(TaskSpec(name="DraftTask", instructions="write draft"))
        target_out = self.allow_dir / "final_output.txt"
        node = ProjectNode(
            node_id=checkpoint_node,
            task_id=t1["task_id"],
            checkpoint={
                "enabled": True,
                "deliver_to": [{"kind": "folder", "path": str(target_out)}],
            },
        )
        spec = ProjectSpec(
            nodes=[node],
            connections=[],
            output_selection=ProjectOutputSelection(items=[{"node_id": checkpoint_node, "role": "draft"}]),
        )
        project = self.project_service.create_project(spec._to_dict())
        project_run_payload = self.project_service.create_project_run(project["project_id"])
        prid = project_run_payload["project_run_id"]

        # Simulate Task Run completion producing draft artifact
        step = self.db.get_project_step(prid, checkpoint_node)
        job_id = self.engine.create_job(
            self.engine.db.get_job(step["active_task_run_id"]) if step.get("active_task_run_id") else
            __import__("relay.models", fromlist=["JobRequest"]).JobRequest(task="draft", worker="codex"),
            queued=True,
        )[0]["job_id"]
        self.db.update_project_step(prid, checkpoint_node, active_task_run_id=job_id, status="running")

        # Create draft artifact
        artifact_dir = self.config.path_value("artifact_root") / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        draft_file = artifact_dir / "draft.txt"
        draft_file.write_text("Original AI Draft", encoding="utf-8")
        digest = hashlib.sha256(draft_file.read_bytes()).hexdigest()
        self.db.add_artifact(
            job_id,
            relative_path="draft.txt",
            final_path=str(draft_file),
            mime_type="text/plain",
            size=draft_file.stat().st_size,
            sha256=digest,
            artifact_uid="art-draft-1",
            role="draft",
        )
        return prid, checkpoint_node, job_id, target_out

    def test_create_pending_approval_pauses_step(self):
        prid, node_id, job_id, target_out = self._setup_project_run_at_checkpoint()
        app = self.approval_service.create_pending_approval(prid, node_id)
        self.assertEqual(app["status"], "pending")
        step = self.db.get_project_step(prid, node_id)
        self.assertEqual(step["status"], "awaiting_approval")

    def test_approve_completes_step_and_delivers(self):
        prid, node_id, job_id, target_out = self._setup_project_run_at_checkpoint()
        app = self.approval_service.create_pending_approval(prid, node_id)
        result = self.approval_service.approve(prid, app["token"], reviewer="reviewer@example.com")
        self.assertEqual(result["approval"]["status"], "approved")
        step = self.db.get_project_step(prid, node_id)
        self.assertEqual(step["status"], "completed")

        # Verify delivery file exists and content matches
        self.assertTrue(target_out.exists())
        self.assertEqual(target_out.read_text(encoding="utf-8"), "Original AI Draft")

    def test_approve_with_edits_creates_new_artifact_and_delivers(self):
        prid, node_id, job_id, target_out = self._setup_project_run_at_checkpoint()
        app = self.approval_service.create_pending_approval(prid, node_id)

        # Prepare edited file
        edit_file = self.root / "human_edit.txt"
        edit_file.write_text("Human Edited Content", encoding="utf-8")

        result = self.approval_service.approve_with_edits(
            prid, app["token"], reviewer="reviewer@example.com", edit_file_path=str(edit_file), role="draft"
        )
        self.assertEqual(result["approval"]["status"], "approved")
        self.assertIsNotNone(result["approval"]["edited_artifact_uid"])

        # Check delivered content is the edited text
        self.assertTrue(target_out.exists())
        self.assertEqual(target_out.read_text(encoding="utf-8"), "Human Edited Content")

    def test_reject_fails_step_and_project_run(self):
        prid, node_id, job_id, target_out = self._setup_project_run_at_checkpoint()
        app = self.approval_service.create_pending_approval(prid, node_id)

        result = self.approval_service.reject(prid, app["token"], reviewer="reviewer@example.com", reason="Not good enough")
        self.assertEqual(result["approval"]["status"], "rejected")
        step = self.db.get_project_step(prid, node_id)
        self.assertEqual(step["status"], "failed")
        prun = self.db.get_project_run(prid)
        self.assertEqual(prun["status"], "failed")
        self.assertFalse(target_out.exists())


if __name__ == "__main__":
    unittest.main()
