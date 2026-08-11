"""A Project step must be able to consume the upstream Run's `result` Artifact.

`result` is the only role guaranteed to be unique per Run, so it is the natural
thing for a connection to bind. It lives under `result_root` rather than
`artifact_root`, which the input resolver used to reject outright.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest, TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.util import new_artifact_uid


class ResultRoleBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.task = self.engine.create_task(TaskSpec(name="upstream", instructions="produce a result"))

    def tearDown(self):
        self.temp.cleanup()

    def _register(self, path: Path, role: str) -> str:
        job, _, _ = self.engine.run_task(self.task["task_id"], queued=True, submitted_via="cli")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"answer": "upstream output"}', encoding="utf-8")
        uid = new_artifact_uid()
        self.db.add_artifact(
            job["job_id"],
            relative_path=path.name,
            final_path=str(path),
            mime_type="application/json",
            size=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            artifact_uid=uid,
            role=role,
        )
        return uid

    def test_result_role_artifact_under_result_root_can_be_bound(self):
        uid = self._register(self.config.path_value("result_root") / "2026-08-06" / "run" / "result.json", "result")

        resolved = self.engine._resolve_artifact_inputs(
            JobRequest(task="x", artifact_inputs=[{"artifact_uid": uid, "alias": "A1"}])
        )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["role"], "result")
        self.assertEqual(resolved[0]["alias"], "A1")

    def test_output_role_artifact_under_artifact_root_still_binds(self):
        uid = self._register(self.config.path_value("artifact_root") / "job" / "portrait.json", "output")

        resolved = self.engine._resolve_artifact_inputs(
            JobRequest(task="x", artifact_inputs=[{"artifact_uid": uid, "alias": "A1"}])
        )

        self.assertEqual(resolved[0]["role"], "output")

    def test_artifact_outside_relay_storage_is_still_refused(self):
        outside = Path(self.temp.name) / "elsewhere" / "secret.json"
        uid = self._register(outside, "output")

        with self.assertRaises(RelayError) as ctx:
            self.engine._resolve_artifact_inputs(
                JobRequest(task="x", artifact_inputs=[{"artifact_uid": uid, "alias": "A1"}])
            )
        self.assertEqual(ctx.exception.code, "ARTIFACT_PATH_VIOLATION")

    def test_tampered_artifact_is_still_refused(self):
        path = self.config.path_value("result_root") / "run" / "result.json"
        uid = self._register(path, "result")
        path.write_text('{"answer": "tampered"}', encoding="utf-8")

        with self.assertRaises(RelayError) as ctx:
            self.engine._resolve_artifact_inputs(
                JobRequest(task="x", artifact_inputs=[{"artifact_uid": uid, "alias": "A1"}])
            )
        self.assertEqual(ctx.exception.code, "ARTIFACT_CHANGED")


class ResultRoleProjectChainTests(unittest.TestCase):
    """End-to-end shape of the Project that surfaced this: pick -> (image, brief)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)
        self.pick = self.engine.create_task(TaskSpec(name="pick", instructions="choose"))
        self.brief = self.engine.create_task(TaskSpec(name="brief", instructions="summarize"))

    def tearDown(self):
        self.runtime.stop()
        self.temp.cleanup()

    def _complete_with_result_file(self, project_run_id: str, node_id: str) -> None:
        """Finish a step the way the engine does: result.json under result_root."""
        step = self.db.get_project_step(project_run_id, node_id)
        job_id = step["active_task_run_id"]
        self.db.update_job(job_id, status="COMPLETED", result_status="complete")
        result_path = self.config.path_value("result_root") / "2026-08-07" / job_id / "result.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text('{"answer": "picked"}', encoding="utf-8")
        self.db.add_artifact(
            job_id,
            relative_path=result_path.name,
            final_path=str(result_path),
            mime_type="application/json",
            size=result_path.stat().st_size,
            sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
            artifact_uid=new_artifact_uid(),
            role="result",
        )

    def test_downstream_step_dispatches_from_a_result_role_connection(self):
        project = self.service.create_project(
            {
                "name": "result-chain",
                "nodes": [
                    {"node_id": "pick", "task_id": self.pick["task_id"]},
                    {"node_id": "brief", "task_id": self.brief["task_id"]},
                ],
                "connections": [{"from_node": "pick", "from_role": "result", "to_node": "brief", "to_alias": "A1"}],
                "output_selection": [{"node_id": "brief", "role": "output"}],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        self.runtime.tick_once()
        self._complete_with_result_file(project_run_id, "pick")
        self.runtime.tick_once()
        self.runtime.tick_once()

        steps = {s["node_id"]: s for s in self.db.list_project_steps(project_run_id)}
        self.assertEqual(steps["pick"]["status"], "completed")
        # The downstream step must actually start, not fail on ARTIFACT_PATH_VIOLATION.
        self.assertNotEqual(steps["brief"]["status"], "failed", steps["brief"].get("error_message"))
        self.assertIsNotNone(steps["brief"]["active_task_run_id"])


if __name__ == "__main__":
    unittest.main()
