from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.comparison.service import ComparisonService
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService


class Phase6bServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.comparison_service = ComparisonService(self.db, self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_compare_task_runs_diffs_artifacts_and_metadata(self):
        job1, _ = self.engine.create_job(JobRequest(task="Task 1", worker="codex"), queued=True)
        job2, _ = self.engine.create_job(JobRequest(task="Task 1", worker="claude"), queued=True)

        res = self.comparison_service.compare_runs(job1["job_id"], job2["job_id"])
        self.assertEqual(res["a_run_id"], job1["job_id"])
        self.assertEqual(res["b_run_id"], job2["job_id"])
        self.assertEqual(res["metadata_diff"]["requested_worker"]["a"], "codex")
        self.assertEqual(res["metadata_diff"]["requested_worker"]["b"], "claude")

    def test_diff_text_artifacts(self):
        job1, _ = self.engine.create_job(JobRequest(task="T1"), queued=True)
        job2, _ = self.engine.create_job(JobRequest(task="T2"), queued=True)

        art1_dir = self.config.path_value("artifact_root") / job1["job_id"]
        art1_dir.mkdir(parents=True, exist_ok=True)
        f1 = art1_dir / "report.md"
        f1.write_text("Line 1\nLine 2\n", encoding="utf-8")

        art2_dir = self.config.path_value("artifact_root") / job2["job_id"]
        art2_dir.mkdir(parents=True, exist_ok=True)
        f2 = art2_dir / "report.md"
        f2.write_text("Line 1\nLine 2 modified\nLine 3\n", encoding="utf-8")

        self.db.add_artifact(
            job1["job_id"],
            relative_path="report.md",
            final_path=str(f1),
            mime_type="text/markdown",
            size=f1.stat().st_size,
            sha256="h1",
            artifact_uid="art-1",
            role="output",
        )
        self.db.add_artifact(
            job2["job_id"],
            relative_path="report.md",
            final_path=str(f2),
            mime_type="text/markdown",
            size=f2.stat().st_size,
            sha256="h2",
            artifact_uid="art-2",
            role="output",
        )

        diff = self.comparison_service.diff_artifacts("art-1", "art-2")
        self.assertEqual(diff["a_artifact_uid"], "art-1")
        self.assertEqual(diff["b_artifact_uid"], "art-2")
        self.assertTrue(diff["diff_available"])
        self.assertIn("-Line 2", "".join(diff["text_diff"]))
        self.assertIn("+Line 2 modified", "".join(diff["text_diff"]))


if __name__ == "__main__":
    unittest.main()


class Phase6bPartialReexecuteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.project_service = ProjectService(self.db, self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def test_partial_reexecute_resets_downstream_nodes(self):
        t1 = self.engine.create_task(TaskSpec(name="T1", instructions="step 1"))
        t2 = self.engine.create_task(TaskSpec(name="T2", instructions="step 2"))
        proj = self.project_service.create_project(
            {
                "name": "Flow",
                "nodes": [
                    {"node_id": "step1", "task_id": t1["task_id"]},
                    {"node_id": "step2", "task_id": t2["task_id"]},
                ],
                "connections": [
                    {"from_node": "step1", "from_role": "out", "to_node": "step2", "to_alias": "A1"},
                ],
                "output_selection": [],
            }
        )
        prun = self.project_service.create_project_run(proj["project_id"])
        prid = prun["project_run_id"]

        # Simulate step1 & step2 completing
        self.db.update_project_step(prid, "step1", status="completed", active_task_run_id="job-1")
        self.db.update_project_step(prid, "step2", status="completed", active_task_run_id="job-2")
        self.db.update_project_run(prid, status="completed")

        # Call partial_reexecute from step2
        res = self.project_service.partial_reexecute(prid, from_node="step2", cascade=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["target_node"], "step2")

        # Verify step1 remains completed, step2 is reset to pending, project_run is running
        s1 = self.db.get_project_step(prid, "step1")
        s2 = self.db.get_project_step(prid, "step2")
        self.assertEqual(s1["status"], "completed")
        self.assertEqual(s1["active_task_run_id"], "job-1")
        self.assertEqual(s2["status"], "pending")
        self.assertIsNone(s2["active_task_run_id"])
        self.assertEqual(self.db.get_project_run(prid)["status"], "running")

    def test_partial_reexecute_worker_override_reaches_child_run(self):
        task = self.engine.create_task(TaskSpec(name="Worker override", instructions="step"))
        project = self.project_service.create_project(
            {
                "name": "Worker flow",
                "nodes": [{"node_id": "step", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run_id = self.project_service.create_project_run(project["project_id"])["project_run_id"]
        runtime = ProjectRuntime(self.db, self.engine, self.project_service)
        runtime.tick_once()
        first_job = self.db.get_project_step(run_id, "step")["active_task_run_id"]
        self.db.update_job(first_job, status="COMPLETED", result_status="complete")
        self.db.update_project_run(run_id, status="completed")

        self.project_service.partial_reexecute(run_id, from_node="step", worker="codex")
        runtime.tick_once()
        second_job = self.db.get_job(self.db.get_project_step(run_id, "step")["active_task_run_id"])

        self.assertEqual(second_job["requested_worker"], "codex")
        self.db.update_job(second_job["job_id"], status="COMPLETED", result_status="complete")
        self.db.update_project_run(run_id, status="completed")
        self.project_service.partial_reexecute(run_id, from_node="step")
        runtime.tick_once()
        third_job = self.db.get_job(self.db.get_project_step(run_id, "step")["active_task_run_id"])
        self.assertEqual(third_job["requested_worker"], "auto")

    def test_partial_reexecute_instruction_addendum_reaches_child_run(self):
        task = self.engine.create_task(TaskSpec(name="Addendum", instructions="original instructions"))
        project = self.project_service.create_project(
            {
                "name": "Addendum flow",
                "nodes": [{"node_id": "step", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run_id = self.project_service.create_project_run(project["project_id"])["project_run_id"]
        runtime = ProjectRuntime(self.db, self.engine, self.project_service)
        runtime.tick_once()
        first_job = self.db.get_project_step(run_id, "step")["active_task_run_id"]
        self.db.update_job(first_job, status="COMPLETED", result_status="complete")
        self.db.update_project_run(run_id, status="completed")

        self.project_service.partial_reexecute(run_id, from_node="step", instruction_addendum="only fix the title")
        runtime.tick_once()
        second_job = self.db.get_job(self.db.get_project_step(run_id, "step")["active_task_run_id"])
        dispatched_task_text = json.loads(second_job["request_json"]).get("task") or ""

        self.assertIn("original instructions", dispatched_task_text)
        self.assertIn("only fix the title", dispatched_task_text)

        # The registered Task's own instructions are never mutated by a one-off addendum.
        stored_task = self.db.get_task(task["task_id"])
        self.assertEqual(stored_task["instructions"], "original instructions")
