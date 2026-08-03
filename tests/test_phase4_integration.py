from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService


class Phase4AcceptanceTests(unittest.TestCase):
    """Acceptance flow: collect -> analyze & chart (parallel) -> final."""

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
        self.runtime.stop()
        self.temp.cleanup()

    def _create_task(self, name: str) -> dict:
        return self.engine.create_task(TaskSpec(name=name, instructions=f"do {name}"))

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
        out_path.write_text(f"{role}-payload", encoding="utf-8")
        digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
        self.db.add_artifact(
            job_id,
            relative_path="result.txt",
            final_path=str(out_path),
            mime_type="text/plain",
            size=out_path.stat().st_size,
            sha256=digest,
            artifact_uid=__import__("relay.util", fromlist=["new_artifact_uid"]).new_artifact_uid(),
            role=role,
        )

    def _drain_ticks(self, project_run_id, total_ticks=20):
        for _ in range(total_ticks):
            self.runtime.tick_once()
            for step in self.db.list_project_steps(project_run_id):
                if step["active_task_run_id"]:
                    role = self._expected_role(step["node_id"])
                    self._complete_step_with_artifact(project_run_id, step["node_id"], role)
            run = self.db.get_project_run(project_run_id)
            if run["status"] in {"completed", "failed"}:
                return run

    def _expected_role(self, node_id: str) -> str:
        return {
            "collect": "raw_data",
            "analyze": "analysis",
            "chart": "chart",
            "final": "final_report",
        }.get(node_id, "out")

    def test_acceptance_flow_completes_with_full_lineage(self):
        collect_task = self._create_task("collect")
        analyze_task = self._create_task("analyze")
        chart_task = self._create_task("chart")
        final_task = self._create_task("final")

        project = self.service.create_project(
            {
                "name": "Weekly report",
                "nodes": [
                    {"node_id": "collect", "task_id": collect_task["task_id"]},
                    {"node_id": "analyze", "task_id": analyze_task["task_id"]},
                    {"node_id": "chart", "task_id": chart_task["task_id"]},
                    {"node_id": "final", "task_id": final_task["task_id"]},
                ],
                "connections": [
                    {"from_node": "collect", "from_role": "raw_data", "to_node": "analyze", "to_alias": "A1"},
                    {"from_node": "collect", "from_role": "source_list", "to_node": "final", "to_alias": "A1"},
                    {"from_node": "analyze", "from_role": "analysis", "to_node": "final", "to_alias": "A2"},
                    {"from_node": "chart", "from_role": "chart", "to_node": "final", "to_alias": "A3"},
                ],
                "output_selection": [{"node_id": "final", "role": "final_report"}],
            }
        )
        run_payload = self.service.create_project_run(project["project_id"])
        project_run_id = run_payload["project_run_id"]

        # Drain until collect is dispatched.
        for _ in range(5):
            self.runtime.tick_once()
            cs = self.db.get_project_step(project_run_id, "collect")
            if cs["status"] == "running":
                break
        self.assertEqual(cs["status"], "running", msg=f"after drain collect was {cs['status']}")
        # Complete collect with two artifacts (raw_data and source_list).
        # Mark complete and add BOTH source artifacts.
        self.db.update_job(cs["active_task_run_id"], status="COMPLETED", result_status="complete")
        artifact_dir = self.config.path_value("artifact_root") / cs["active_task_run_id"]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        new_au = __import__("relay.util", fromlist=["new_artifact_uid"]).new_artifact_uid
        for role in ("raw_data", "source_list"):
            out_path = artifact_dir / f"{role}.txt"
            out_path.write_text(f"{role}-payload", encoding="utf-8")
            self.db.add_artifact(
                cs["active_task_run_id"],
                relative_path=f"{role}.txt",
                final_path=str(out_path),
                mime_type="text/plain",
                size=out_path.stat().st_size,
                sha256=hashlib.sha256(out_path.read_bytes()).hexdigest(),
                artifact_uid=new_au(),
                role=role,
            )

        # Now drain ticks: analyze and chart become ready in parallel.
        run = None
        for _ in range(20):
            self.runtime.tick_once()
            for step in self.db.list_project_steps(project_run_id):
                if step["active_task_run_id"]:
                    self._complete_step_with_artifact(
                        project_run_id, step["node_id"], self._expected_role(step["node_id"])
                    )
            run = self.db.get_project_run(project_run_id)
            if run["status"] in {"completed", "failed"}:
                break

        self.assertEqual(run["status"], "completed", f"final state was {run['status']}")
        steps = {s["node_id"]: s for s in self.db.list_project_steps(project_run_id)}
        for node_id in ("collect", "analyze", "chart", "final"):
            self.assertEqual(steps[node_id]["status"], "completed")

        # Verify each node produced exactly one Task Run
        for node_id in ("collect", "analyze", "chart", "final"):
            step_runs = self.db.list_project_step_runs(project_run_id, node_id)
            self.assertEqual(len(step_runs), 1, f"{node_id} should have exactly 1 task run")
            self.assertTrue(step_runs[0]["task_run_id"])

        # Verify final_artifact_ids resolved to the final node's role
        import json as _json

        final_ids = _json.loads(run["final_artifact_ids_json"] or "[]")
        self.assertEqual(len(final_ids), 1)
        self.assertEqual(final_ids[0]["node_id"], "final")
        self.assertEqual(final_ids[0]["role"], "final_report")

    def test_restart_does_not_duplicate_dispatch(self):
        collect_task = self._create_task("collect")
        analyze_task = self._create_task("analyze")
        project = self.service.create_project(
            {
                "name": "Restart",
                "nodes": [
                    {"node_id": "collect", "task_id": collect_task["task_id"]},
                    {"node_id": "analyze", "task_id": analyze_task["task_id"]},
                ],
                "connections": [
                    {"from_node": "collect", "from_role": "raw", "to_node": "analyze", "to_alias": "A1"},
                ],
                "output_selection": [],
            }
        )
        run_payload = self.service.create_project_run(project["project_id"])
        project_run_id = run_payload["project_run_id"]

        self.runtime.tick_once()
        first_collect = self.db.get_project_step(project_run_id, "collect")
        first_active = first_collect["active_task_run_id"]
        first_step_runs = self.db.list_project_step_runs(project_run_id, "collect")
        self.assertEqual(len(first_step_runs), 1)

        # Simulate daemon restart with a new runtime over the same DB.
        new_runtime = ProjectRuntime(self.db, self.engine, self.service)
        new_runtime.tick_once()
        second_collect = self.db.get_project_step(project_run_id, "collect")
        self.assertEqual(second_collect["active_task_run_id"], first_active)
        second_step_runs = self.db.list_project_step_runs(project_run_id, "collect")
        self.assertEqual(len(second_step_runs), 1)

    def test_connection_artifact_is_passed_to_downstream_task_run(self):
        source = self._create_task("source")
        consumer = self._create_task("consumer")
        project = self.service.create_project(
            {
                "name": "Artifact handoff",
                "nodes": [
                    {"node_id": "source", "task_id": source["task_id"]},
                    {"node_id": "consumer", "task_id": consumer["task_id"]},
                ],
                "connections": [
                    {"from_node": "source", "from_role": "report", "to_node": "consumer", "to_alias": "A1"}
                ],
                "output_selection": [],
            }
        )
        project_run_id = self.service.create_project_run(project["project_id"])["project_run_id"]
        self.runtime.tick_once()
        source_step = self.db.get_project_step(project_run_id, "source")
        source_job_id = source_step["active_task_run_id"]
        artifact_dir = self.config.path_value("artifact_root") / source_job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_file = artifact_dir / "report.md"
        artifact_file.write_text("handoff", encoding="utf-8")
        self.db.add_artifact(
            source_job_id,
            relative_path="report.md",
            final_path=str(artifact_file),
            mime_type="text/markdown",
            size=artifact_file.stat().st_size,
            sha256=hashlib.sha256(artifact_file.read_bytes()).hexdigest(),
            artifact_uid="artifact-handoff",
            role="report",
        )
        self.db.update_job(source_job_id, status="COMPLETED", result_status="complete")

        self.runtime.tick_once()
        self.runtime.tick_once()

        consumer_step = self.db.get_project_step(project_run_id, "consumer")
        consumer_job = self.db.get_job(consumer_step["active_task_run_id"])
        self.assertEqual(consumer_job["caller"], "service")
        manifest = __import__("json").loads(consumer_job["input_manifest_json"])
        self.assertEqual(manifest[0]["alias"], "A1")
        self.assertEqual(manifest[0]["artifact_uid"], "artifact-handoff")
        lineage = self.db.lineage_for_job(consumer_job["job_id"])
        self.assertEqual(lineage[0]["source_artifact_uid"], "artifact-handoff")


if __name__ == "__main__":
    unittest.main()
