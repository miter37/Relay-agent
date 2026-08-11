from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.orchestrator.planner import (
    Evidence,
    build_evidence,
    build_output_selection_evidence,
    plan_repair,
)
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.util import new_artifact_uid


class PlanRepairPureTests(unittest.TestCase):
    def test_daemon_restarted_is_a_plain_retry(self):
        evidence = Evidence(node_id="a", error_code="DAEMON_RESTARTED", error_message="restarted mid-run")
        decision = plan_repair(evidence)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.strategy, "retry")
        self.assertEqual(decision.node_id, "a")

    def test_process_crashed_is_a_plain_retry(self):
        evidence = Evidence(node_id="a", error_code="PROCESS_CRASHED", error_message="crashed")
        decision = plan_repair(evidence)
        self.assertEqual(decision.strategy, "retry")

    def test_single_candidate_connection_role_rebind(self):
        evidence = Evidence(
            node_id="page",
            error_code="PROJECT_ARTIFACT_MISSING",
            error_message="missing",
            requested_role="result",
            to_alias="A1",
            available_roles=["output"],
        )
        decision = plan_repair(evidence)
        self.assertEqual(decision.strategy, "rebind_connection")
        self.assertEqual(decision.connection_overrides, {"A1": "output"})

    def test_single_candidate_output_role_rebind(self):
        evidence = Evidence(
            node_id="page",
            error_code="PROJECT_ARTIFACT_MISSING",
            error_message="missing",
            requested_role="result",
            to_alias=None,
            available_roles=["output"],
        )
        decision = plan_repair(evidence)
        self.assertEqual(decision.strategy, "rebind_output_role")
        self.assertEqual(decision.output_role_override, "output")

    def test_zero_candidate_roles_is_not_resolvable_deterministically(self):
        evidence = Evidence(
            node_id="page",
            error_code="PROJECT_ARTIFACT_MISSING",
            error_message="missing",
            requested_role="result",
            to_alias="A1",
            available_roles=[],
        )
        self.assertIsNone(plan_repair(evidence))

    def test_ambiguous_roles_are_not_resolvable_deterministically(self):
        evidence = Evidence(
            node_id="page",
            error_code="PROJECT_ARTIFACT_MISSING",
            error_message="missing",
            requested_role="result",
            to_alias="A1",
            available_roles=["output", "draft"],
        )
        self.assertIsNone(plan_repair(evidence))

    def test_artifact_ambiguous_error_code_always_escalates(self):
        evidence = Evidence(
            node_id="page",
            error_code="PROJECT_ARTIFACT_AMBIGUOUS",
            error_message="ambiguous",
            to_alias="A1",
            available_roles=["output"],
        )
        self.assertIsNone(plan_repair(evidence))

    def test_unavailable_worker_with_one_alternative_swaps(self):
        evidence = Evidence(
            node_id="a",
            error_code="WORKER_DISABLED",
            error_message="disabled",
            requested_worker="antigravity",
            available_workers=["claude"],
        )
        decision = plan_repair(evidence)
        self.assertEqual(decision.strategy, "retry_with_worker")
        self.assertEqual(decision.worker, "claude")

    def test_unavailable_worker_with_several_alternatives_escalates(self):
        evidence = Evidence(
            node_id="a",
            error_code="WORKER_DISABLED",
            error_message="disabled",
            requested_worker="antigravity",
            available_workers=["claude", "codex"],
        )
        self.assertIsNone(plan_repair(evidence))

    def test_unavailable_worker_with_no_alternative_escalates(self):
        evidence = Evidence(
            node_id="a",
            error_code="WORKER_DISABLED",
            error_message="disabled",
            requested_worker="antigravity",
            available_workers=[],
        )
        self.assertIsNone(plan_repair(evidence))

    def test_unrecognized_error_code_escalates(self):
        evidence = Evidence(node_id="a", error_code="SOME_UNKNOWN_ERROR", error_message="?")
        self.assertIsNone(plan_repair(evidence))

    def test_none_error_code_escalates(self):
        evidence = Evidence(node_id="a", error_code=None, error_message=None)
        self.assertIsNone(plan_repair(evidence))


class _OrchestratorPlannerHarness(unittest.TestCase):
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

    def _complete_step_with_role(self, project_run_id: str, node_id: str, role: str) -> None:
        step = self.db.get_project_step(project_run_id, node_id)
        job_id = step["active_task_run_id"]
        self.db.update_job(job_id, status="COMPLETED", result_status="complete")
        artifact_dir = self.config.path_value("artifact_root") / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{role}.txt"
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


class BuildEvidenceTests(_OrchestratorPlannerHarness):
    def test_build_evidence_for_connection_role_mismatch(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Mismatch",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        self._complete_step_with_role(project_run_id, "a", role="output")
        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_step(project_run_id, "b")["status"] in {"failed", "blocked"}:
                break

        evidence = build_evidence(self.db, self.engine, project_run_id, "b")
        self.assertEqual(evidence.error_code, "PROJECT_ARTIFACT_MISSING")
        self.assertEqual(evidence.to_alias, "A1")
        self.assertEqual(evidence.requested_role, "result")
        self.assertEqual(evidence.available_roles, ["output"])

        decision = plan_repair(evidence)
        self.assertEqual(decision.strategy, "rebind_connection")
        self.assertEqual(decision.connection_overrides, {"A1": "output"})

    def test_build_evidence_truncates_error_message(self):
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
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        long_message = "x" * 5000
        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        self.db.update_job(job_id, status="FAILED", error_code="ALL_WORKERS_FAILED", error_message=long_message)
        self.runtime.tick_once()

        evidence = build_evidence(self.db, self.engine, project_run_id, "a")
        self.assertLessEqual(len(evidence.error_message), 2000)
        self.assertNotEqual(evidence.error_message, long_message)

    def test_build_evidence_never_includes_artifact_content(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Mismatch",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        self._complete_step_with_role(project_run_id, "a", role="output")
        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_step(project_run_id, "b")["status"] in {"failed", "blocked"}:
                break

        evidence = build_evidence(self.db, self.engine, project_run_id, "b")
        for field_value in (evidence.error_message, str(evidence.available_roles), str(evidence.log_tail)):
            self.assertNotIn("output-payload", field_value)

    def test_build_evidence_caps_log_tail_at_forty_lines(self):
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
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]

        log_dir = Path(self.temp.name) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = log_dir / "stderr.log"
        stderr_path.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
        self.db.create_attempt(
            job_id=job_id,
            worker="claude",
            started_at="2026-08-10T00:00:00+00:00",
            status="FAILED",
            stderr_path=str(stderr_path),
        )
        self.db.update_job(job_id, status="FAILED", error_code="DAEMON_RESTARTED", error_message="restarted")
        self.runtime.tick_once()

        evidence = build_evidence(self.db, self.engine, project_run_id, "a")
        self.assertLessEqual(len(evidence.log_tail), 40)
        self.assertEqual(evidence.log_tail[-1], "line 99")

    def test_build_evidence_worker_disabled_offers_enabled_alternative(self):
        a = self._task("A")
        # claude and codex are enabled by default; antigravity is not (see relay/config.py).
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        self.db.update_job(job_id, requested_worker="antigravity")
        self.db.update_job(job_id, status="FAILED", error_code="WORKER_DISABLED", error_message="disabled")
        self.runtime.tick_once()

        evidence = build_evidence(self.db, self.engine, project_run_id, "a")
        self.assertEqual(evidence.requested_worker, "antigravity")
        self.assertIn("claude", evidence.available_workers)
        self.assertNotIn("antigravity", evidence.available_workers)


class BuildOutputSelectionEvidenceTests(_OrchestratorPlannerHarness):
    def test_build_output_selection_evidence_repairs_via_planner(self):
        a = self._task("A")
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [{"node_id": "a", "role": "result"}],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        self._complete_step_with_role(project_run_id, "a", role="output")
        self.runtime.tick_once()

        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "failed")

        evidence = build_output_selection_evidence(self.db, self.engine, project_run_id, "a", "result")
        self.assertIsNotNone(evidence)
        decision = plan_repair(evidence)
        self.assertEqual(decision.strategy, "rebind_output_role")
        self.assertEqual(decision.output_role_override, "output")

    def test_build_output_selection_evidence_returns_none_without_task_run(self):
        a = self._task("A")
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [{"node_id": "a", "role": "result"}],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        evidence = build_output_selection_evidence(self.db, self.engine, project_run_id, "a", "result")
        self.assertIsNone(evidence)


if __name__ == "__main__":
    unittest.main()
