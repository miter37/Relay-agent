from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.projects.models import (
    ProjectNode,
    ProjectOutputSelection,
    ProjectSpec,
)
from relay.projects.service import ProjectService
from relay.rpc import RPCClient


class Phase6aAPITests(unittest.TestCase):
    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.config.set("daemon_port", self._free_port())

        self.allow_dir = self.root / "deliveries"
        self.allow_dir.mkdir(parents=True, exist_ok=True)
        self.config.set("allowed_delivery_roots", [str(self.allow_dir)])

        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()

        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(5.0))

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except Exception:
                pass
            self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_approval_api_routes(self):
        db = Database(self.config.path_value("database_path"))
        engine = RelayEngine(self.config, db)
        ps = ProjectService(db, engine)

        t1 = engine.create_task(TaskSpec(name="T1", instructions="draft"))
        target_out = self.allow_dir / "out.txt"

        node = ProjectNode(
            node_id="review",
            task_id=t1["task_id"],
            checkpoint={
                "enabled": True,
                "deliver_to": [{"kind": "folder", "path": str(target_out)}],
            },
        )
        spec = ProjectSpec(
            nodes=[node],
            connections=[],
            output_selection=ProjectOutputSelection(items=[]),
        )
        proj = ps.create_project(spec._to_dict())
        prun = ps.create_project_run(proj["project_id"])
        prid = prun["project_run_id"]

        # Run tick to pause at checkpoint
        self.daemon.project_runtime.tick_once()

        # Step active_task_run_id is created; complete the task run
        step = db.get_project_step(prid, "review")
        db.update_job(step["active_task_run_id"], status="COMPLETED", result_status="complete")

        # Create draft artifact so delivery succeeds
        art_dir = self.config.path_value("artifact_root") / step["active_task_run_id"]
        art_dir.mkdir(parents=True, exist_ok=True)
        draft = art_dir / "draft.txt"
        draft.write_text("API Draft Text", encoding="utf-8")
        db.add_artifact(
            step["active_task_run_id"],
            relative_path="draft.txt",
            final_path=str(draft),
            mime_type="text/plain",
            size=draft.stat().st_size,
            sha256="abc",
            artifact_uid="art-api-draft",
            role="draft",
        )

        self.daemon.project_runtime.tick_once()

        # Get list of approvals via API
        apps = self.client.request("GET", f"/v1/project-runs/{prid}/approvals")
        self.assertTrue(apps["ok"])
        self.assertEqual(len(apps["approvals"]), 1)
        token = apps["approvals"][0]["token"]

        # Approve via API
        app_res = self.client.request(
            "POST",
            f"/v1/project-runs/{prid}/approvals/{token}/approve",
            {"reviewer": "test_user"},
        )
        self.assertTrue(app_res["ok"])
        self.assertEqual(app_res["approval"]["status"], "approved")

        # Check delivered file
        self.assertTrue(target_out.exists())
        self.assertEqual(target_out.read_text(encoding="utf-8"), "API Draft Text")


if __name__ == "__main__":
    unittest.main()
