from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService


def _task(engine: RelayEngine, name: str) -> dict:
    spec = TaskSpec(name=name, instructions=f"do {name}")
    return engine.create_task(spec)


class ProjectRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)

    def tearDown(self):
        self.temp.cleanup()

    def _complete_step_with_artifact(self, project_run_id, node_id, role):
        step = self.db.get_project_step(project_run_id, node_id)
        job_id = step["active_task_run_id"]
        self.db.update_job(job_id, status="COMPLETED", result_status="complete")
        existing = [a for a in self.db.artifacts_for_job(job_id) if a.get("role") == role]
        if existing:
            return
        artifact_dir = self.config.path_value("artifact_root") / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        out_path = artifact_dir / "result.txt"
        out_path.write_text("done", encoding="utf-8")
        digest = __import__("hashlib").sha256(out_path.read_bytes()).hexdigest()
        size = out_path.stat().st_size
        self.db.add_artifact(
            job_id,
            relative_path="result.txt",
            final_path=str(out_path),
            mime_type="text/plain",
            size=size,
            sha256=digest,
            artifact_uid=__import__("relay.util", fromlist=["new_artifact_uid"]).new_artifact_uid(),
            role=role,
        )

    def _make_linear_project(self) -> dict:
        task_a = _task(self.engine, "TA")
        task_b = _task(self.engine, "TB")
        project = self.service.create_project(
            {
                "name": "Linear",
                "nodes": [
                    {"node_id": "a", "task_id": task_a["task_id"]},
                    {"node_id": "b", "task_id": task_b["task_id"]},
                ],
                "connections": [
                    {"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"},
                ],
                "output_selection": [],
            }
        )
        return self.service.create_project_run(project["project_id"])

    def test_root_nodes_are_ready(self):
        project_run = self._make_linear_project()
        steps = self.db.list_project_steps(project_run["project_run_id"])
        statuses = {s["node_id"]: s["status"] for s in steps}
        self.assertEqual(statuses["a"], "ready")
        self.assertEqual(statuses["b"], "pending")

    def test_dispatch_creates_task_run_and_records_step_run(self):
        project_run = self._make_linear_project()
        self.runtime.tick_once()
        step_a = self.db.get_project_step(project_run["project_run_id"], "a")
        self.assertEqual(step_a["status"], "running")
        self.assertTrue(step_a["active_task_run_id"])
        step_runs = self.db.list_project_step_runs(project_run["project_run_id"], "a")
        self.assertEqual(len(step_runs), 1)

    def test_completing_a_root_makes_descendant_ready(self):
        project_run = self._make_linear_project()
        self.runtime.tick_once()
        self._complete_step_with_artifact(project_run["project_run_id"], "a", "out")
        self.runtime.tick_once()
        step_b = self.db.get_project_step(project_run["project_run_id"], "b")
        self.assertEqual(step_b["status"], "ready")

    def test_partial_task_run_is_terminal_for_project_progression(self):
        project_run = self._make_linear_project()
        project_run_id = project_run["project_run_id"]
        self.runtime.tick_once()
        self._complete_step_with_artifact(project_run_id, "a", "out")
        step_a = self.db.get_project_step(project_run_id, "a")
        self.db.update_job(step_a["active_task_run_id"], status="PARTIAL", result_status="partial")

        self.runtime.tick_once()
        step_a = self.db.get_project_step(project_run_id, "a")
        step_b = self.db.get_project_step(project_run_id, "b")
        self.assertEqual(step_a["status"], "completed")
        self.assertEqual(step_b["status"], "ready")

        self.runtime.tick_once()
        self._complete_step_with_artifact(project_run_id, "b", "out")
        self.runtime.tick_once()
        final_run = self.db.get_project_run(project_run_id)
        self.assertEqual(final_run["status"], "completed")
        self.assertIn("TASK_RUN_PARTIAL", final_run["warnings_json"])

    def test_completing_all_runs_marks_run_completed(self):
        project_run = self._make_linear_project()
        for _ in range(15):
            self.runtime.tick_once()
            for step in self.db.list_project_steps(project_run["project_run_id"]):
                if step["active_task_run_id"]:
                    self._complete_step_with_artifact(project_run["project_run_id"], step["node_id"], "out")
        final_run = self.db.get_project_run(project_run["project_run_id"])
        self.assertEqual(final_run["status"], "completed")

    def test_failed_step_marks_run_failed_and_caches_error(self):
        project_run = self._make_linear_project()
        self.runtime.tick_once()
        step_a = self.db.get_project_step(project_run["project_run_id"], "a")
        self.db.update_job(
            step_a["active_task_run_id"], status="FAILED", error_code="ALL_WORKERS_FAILED", error_message="boom"
        )
        for _ in range(5):
            self.runtime.tick_once()
        final_run = self.db.get_project_run(project_run["project_run_id"])
        self.assertEqual(final_run["status"], "failed")
        self.assertIn("ALL_WORKERS_FAILED", final_run["warnings_json"])

    def test_missing_selected_final_artifact_fails_project_run(self):
        task = _task(self.engine, "Final")
        project = self.service.create_project(
            {
                "name": "Strict final output",
                "nodes": [{"node_id": "final", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [{"node_id": "final", "role": "report"}],
            }
        )
        project_run_id = self.service.create_project_run(project["project_id"])["project_run_id"]
        self.runtime.tick_once()
        step = self.db.get_project_step(project_run_id, "final")
        self.db.update_job(step["active_task_run_id"], status="COMPLETED", result_status="complete")

        for _ in range(3):
            self.runtime.tick_once()

        run = self.db.get_project_run(project_run_id)
        self.assertEqual(run["status"], "failed")
        self.assertIn("PROJECT_ARTIFACT_MISSING", run["warnings_json"])

    def test_runtime_does_not_double_dispatch_after_restart(self):
        project_run = self._make_linear_project()
        self.runtime.tick_once()
        step_a = self.db.get_project_step(project_run["project_run_id"], "a")
        first_active = step_a["active_task_run_id"]
        # Simulate a daemon restart: build a new runtime over the same DB.
        runtime2 = ProjectRuntime(self.db, self.engine, self.service)
        runtime2.tick_once()
        step_a2 = self.db.get_project_step(project_run["project_run_id"], "a")
        self.assertEqual(step_a2["active_task_run_id"], first_active)
        step_runs = self.db.list_project_step_runs(project_run["project_run_id"], "a")
        self.assertEqual(len(step_runs), 1)


if __name__ == "__main__":
    unittest.main()
