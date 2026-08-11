"""A step that fails before producing a Task Run must not strand the Project Run.

Dispatch-time failures (missing Task snapshot, unresolvable inputs, a rejected
JobRequest) used to leave descendants 'pending' forever, so the run stayed
'running' indefinitely and `project-run retry` refused it as non-terminal.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.util import new_artifact_uid


class DispatchFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)
        self.first = self.engine.create_task(TaskSpec(name="first", instructions="do first"))
        self.second = self.engine.create_task(TaskSpec(name="second", instructions="do second"))
        self.third = self.engine.create_task(TaskSpec(name="third", instructions="do third"))

    def tearDown(self):
        self.runtime.stop()
        self.temp.cleanup()

    def _project(self) -> dict:
        return self.service.create_project(
            {
                "name": "chain",
                "nodes": [
                    {"node_id": "a", "task_id": self.first["task_id"]},
                    {"node_id": "b", "task_id": self.second["task_id"]},
                    {"node_id": "c", "task_id": self.third["task_id"]},
                ],
                "connections": [
                    {"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"},
                    {"from_node": "b", "from_role": "output", "to_node": "c", "to_alias": "A1"},
                ],
                "output_selection": [{"node_id": "c", "role": "output"}],
            }
        )

    def _complete_step(self, project_run_id: str, node_id: str, role: str) -> None:
        """Finish a step without a real Worker, leaving one Artifact in the given role."""
        step = self.db.get_project_step(project_run_id, node_id)
        job_id = step["active_task_run_id"]
        self.db.update_job(job_id, status="COMPLETED", result_status="complete")
        artifact_dir = self.config.path_value("artifact_root") / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / "result.txt"
        path.write_text(f"{role}-payload", encoding="utf-8")
        self.db.add_artifact(
            job_id,
            relative_path=path.name,
            final_path=str(path),
            mime_type="text/plain",
            size=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            artifact_uid=new_artifact_uid(),
            role=role,
        )

    def test_dispatch_failure_blocks_descendants_and_finalizes_the_run(self):
        project = self._project()
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        # Make node b undispatchable: drop its Task snapshot so dispatch fails
        # before any Task Run exists, which is what a mid-chain failure looks like.
        payload = json.loads(self.db.get_project_run(project_run_id)["project_snapshot_json"])
        payload["task_snapshots"].pop(self.second["task_id"], None)
        self.db.update_project_run(project_run_id, project_snapshot_json=json.dumps(payload))

        self.runtime.tick_once()
        self._complete_step(project_run_id, "a", "result")
        for _ in range(8):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed", "cancelled"}:
                break

        steps = {s["node_id"]: s for s in self.db.list_project_steps(project_run_id)}
        self.assertEqual(steps["a"]["status"], "completed")
        self.assertEqual(steps["b"]["status"], "failed")
        self.assertEqual(steps["b"]["error_code"], "PROJECT_TASK_MISSING")
        # c must not be left pending forever.
        self.assertIn(steps["c"]["status"], {"blocked", "failed", "cancelled"})
        # And the run itself must reach a terminal state so it can be retried.
        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
