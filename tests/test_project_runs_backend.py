"""Backend regressions for the Project Runs GUI screen.

Pins the backend fixes called out in ``docs/Relay_GUI_Project_Runs_Screen_Design_v1.0.md``
section 8:

1. ``project_runs.started_at`` is recorded on first dispatch, not overwritten by
   subsequent dispatches, and cleared on retry/partial-reexecute so the next
   dispatch re-records it.
2. ``/v1/catalog/project-runs`` exposes ``project_name``, ``failed_node_id``,
   ``blocked_step_count``, and ``started_at`` on each item so the GUI list does
   not need an N+1 fetch per row.
3. ``/v1/project-runs/{id}/steps`` exposes ``attempt_count`` on each step so
   the GUI does not need to query ``project_step_runs`` separately.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from relay.api import catalog_project_runs
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.util import new_artifact_uid


class _ProjectRunHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)

    def tearDown(self) -> None:
        self.runtime.stop()
        self.temp.cleanup()

    def _task(self, name: str) -> dict:
        return self.engine.create_task(TaskSpec(name=name, instructions=f"do {name}"))

    def _complete_step(self, project_run_id: str, node_id: str, role: str = "out") -> None:
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


class ProjectRunStartedAtTests(_ProjectRunHarness):
    def test_started_at_is_null_before_dispatch_and_recorded_on_first_dispatch(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Linear",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        # Before any dispatch the column is unset.
        self.assertIsNone(self.db.get_project_run(project_run_id)["started_at"])

        self.runtime.tick_once()
        started_after_first = self.db.get_project_run(project_run_id)["started_at"]
        self.assertTrue(started_after_first, "started_at must be set after first dispatch")

        # Completing a second step dispatch must not overwrite the first timestamp.
        self._complete_step(project_run_id, "a", role="out")
        for _ in range(8):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed", "cancelled"}:
                break

        started_after_second = self.db.get_project_run(project_run_id)["started_at"]
        self.assertEqual(started_after_second, started_after_first)

    def test_retry_resets_started_at_so_next_dispatch_re_records(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Retry",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        self.runtime.tick_once()
        first_started = self.db.get_project_run(project_run_id)["started_at"]
        self.assertTrue(first_started)

        # Force the run into a failed terminal state without breaking b's task snapshot
        # so the next tick can re-dispatch b and re-record started_at.
        self._complete_step(project_run_id, "a", role="out")
        for _ in range(8):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed"}:
                break
        self.db.update_project_step(
            project_run_id, "b", status="failed", error_code="SIMULATED_FAILURE", error_message="x"
        )
        self.db.update_project_run(
            project_run_id,
            status="failed",
            warnings_json=json.dumps([{"node_id": "b", "error_code": "SIMULATED_FAILURE", "error_message": "x"}]),
            completed_at=None,
        )
        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "failed")

        # Retry clears started_at so the next dispatch re-records a fresh timestamp.
        self.service.retry_project_run(project_run_id)
        self.assertIsNone(self.db.get_project_run(project_run_id)["started_at"])

        # Tick: a is already completed; b becomes ready and dispatches a fresh Task Run.
        for _ in range(8):
            self.runtime.tick_once()
            step_b = self.db.get_project_step(project_run_id, "b")
            if step_b["status"] == "running":
                break

        second_started = self.db.get_project_run(project_run_id)["started_at"]
        self.assertTrue(second_started, "started_at must be re-populated after retry dispatch")


class ProjectRunCatalogFieldsTests(_ProjectRunHarness):
    def test_catalog_exposes_failed_node_blocked_count_project_name_and_started_at(self):
        a = self._task("A")
        b = self._task("B")
        c = self._task("C")
        project = self.service.create_project(
            {
                "name": "Daily briefing",
                "project_summary": "Daily briefing pipeline.",
                "nodes": [
                    {"node_id": "pick", "task_id": a["task_id"]},
                    {"node_id": "image", "task_id": b["task_id"]},
                    {"node_id": "page", "task_id": c["task_id"]},
                ],
                "connections": [
                    {"from_node": "pick", "from_role": "result", "to_node": "image", "to_alias": "A1"},
                    {"from_node": "image", "from_role": "output", "to_node": "page", "to_alias": "A1"},
                ],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        # Make image fail before producing a Task Run so page is blocked.
        payload = json.loads(self.db.get_project_run(project_run_id)["project_snapshot_json"])
        payload["task_snapshots"].pop(b["task_id"], None)
        self.db.update_project_run(project_run_id, project_snapshot_json=json.dumps(payload))

        self.runtime.tick_once()
        self._complete_step(project_run_id, "pick", role="result")
        for _ in range(10):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] == "failed":
                break

        steps = {s["node_id"]: s for s in self.db.list_project_steps(project_run_id)}
        self.assertEqual(steps["pick"]["status"], "completed")
        self.assertEqual(steps["image"]["status"], "failed")
        self.assertEqual(steps["page"]["status"], "blocked")
        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "failed")

        items = catalog_project_runs(self.db, project_id=project["project_id"])["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["project_name"], "Daily briefing")
        self.assertEqual(item["failed_node_id"], "image")
        self.assertEqual(item["failed_step_count"], 1)
        self.assertEqual(item["blocked_step_count"], 1)
        self.assertEqual(item["step_count"], 3)
        self.assertTrue(item["started_at"], "started_at must be populated once dispatch happened")
        self.assertEqual(item["status"], "failed")

    def test_catalog_running_run_has_no_failed_node_or_blocked_steps(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Two-step",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        self.service.create_project_run(project["project_id"])

        items = catalog_project_runs(self.db, project_id=project["project_id"])["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["project_name"], "Two-step")
        self.assertIsNone(item["failed_node_id"])
        self.assertEqual(item["failed_step_count"], 0)
        self.assertEqual(item["blocked_step_count"], 0)


class ProjectStepAttemptCountTests(_ProjectRunHarness):
    def test_attempt_count_reflects_dispatch_rows(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Attempt count",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        self.runtime.tick_once()
        steps = {s["node_id"]: s for s in self.db.list_project_steps(project_run_id)}
        self.assertEqual(steps["a"]["attempt_count"], 1)

        # Force the run into a failed terminal state with a left as completed,
        # then retry from b so the next tick re-dispatches b and bumps its attempt count.
        self._complete_step(project_run_id, "a", role="out")
        for _ in range(8):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed"}:
                break
        self.db.update_project_step(
            project_run_id, "b", status="failed", error_code="SIMULATED_FAILURE", error_message="x"
        )
        self.db.update_project_run(
            project_run_id,
            status="failed",
            warnings_json=json.dumps([{"node_id": "b", "error_code": "SIMULATED_FAILURE", "error_message": "x"}]),
            completed_at=None,
        )

        self.service.retry_project_run(project_run_id)
        for _ in range(8):
            self.runtime.tick_once()
            step_b = self.db.get_project_step(project_run_id, "b")
            if step_b["status"] == "running":
                break

        steps = {s["node_id"]: s for s in self.db.list_project_steps(project_run_id)}
        self.assertGreaterEqual(steps["b"]["attempt_count"], 2)


class EnsureProjectRunStartedUnitTests(_ProjectRunHarness):
    def test_ensure_started_at_records_first_dispatch_and_is_idempotent(self):
        project = self.service.create_project(
            {
                "name": "Idempotent started_at",
                "nodes": [{"node_id": "only", "task_id": self._task("O")["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        self.assertTrue(self.db.ensure_project_run_started(project_run_id, "2026-08-01T00:00:00+00:00"))
        self.assertEqual(
            self.db.get_project_run(project_run_id)["started_at"],
            "2026-08-01T00:00:00+00:00",
        )
        # A second ensure call must not overwrite the original timestamp.
        self.assertFalse(self.db.ensure_project_run_started(project_run_id, "2026-08-02T00:00:00+00:00"))
        self.assertEqual(
            self.db.get_project_run(project_run_id)["started_at"],
            "2026-08-01T00:00:00+00:00",
        )


class ProjectStepAttemptCountApiTests(_ProjectRunHarness):
    def test_api_steps_response_carries_attempt_count(self):
        a = self._task("A")
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        self.runtime.tick_once()
        from relay.api import project_run_steps

        steps_response = project_run_steps(self.engine, run["project_run_id"])["steps"]
        self.assertEqual(len(steps_response), 1)
        self.assertEqual(steps_response[0]["node_id"], "a")
        self.assertEqual(steps_response[0]["attempt_count"], 1)


if __name__ == "__main__":
    unittest.main()
