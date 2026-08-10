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
from relay.orchestrator.planner import RepairDecision
from relay.orchestrator.supervisor import Supervisor
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.util import new_artifact_uid


class _StubAgent:
    """A duck-typed stand-in for OrchestratorAgent - the plan's Task 5 tests are
    supposed to stub the agent rather than dispatch a real LLM Task Run."""

    def __init__(self, decision: RepairDecision | None = None, *, raises: Exception | None = None):
        self._decision = decision
        self._raises = raises
        self.calls = 0

    def decide(self, evidence, state_digest):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._decision


class _SupervisorHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)
        self.stub_agent = _StubAgent()
        self.supervisor = Supervisor(self.db, self.engine, agent_factory=lambda engine, config: self.stub_agent)

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

    def _attach_orchestrator(self, project_run_id: str, orchestrator_config: dict) -> None:
        """Inject orchestrator config into an already-created Run's snapshot, after the
        raw failure state was reached undisturbed via a plain (non-orchestrator) Project.

        ProjectRuntime now automatically consults its own Supervisor the moment a step
        fails (Task 6), so a Project declaring the Orchestrator from the start would race
        these tests' explicit ``on_step_failed`` calls - the runtime's own default
        Supervisor (a real, unstubbed one) would consume the failure first. Attaching the
        config only after the raw failure is reached keeps these tests exercising
        ``Supervisor.on_step_failed`` in isolation, via ``self.supervisor`` (stub-wired).
        """
        run = self.db.get_project_run(project_run_id)
        snapshot = json.loads(run["project_snapshot_json"])
        snapshot["project_definition"]["orchestrator"] = orchestrator_config
        self.db.update_project_run(project_run_id, project_snapshot_json=json.dumps(snapshot))

    def _mismatched_connection_project_run(self, orchestrator_config: dict) -> str:
        """A connection role mismatch that Tier 0 cannot resolve deterministically: the
        upstream node emits two roles, neither matching the declared one, so
        ``plan_repair`` sees an ambiguous candidate set and escalates to Tier 1."""
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
        self._complete_step_with_role(project_run_id, "a", role="extra")
        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_step(project_run_id, "b")["status"] in {"failed", "blocked"}:
                break
        self.assertEqual(self.db.get_project_step(project_run_id, "b")["status"], "failed")
        self._attach_orchestrator(project_run_id, orchestrator_config)
        return project_run_id

    def _transient_failure_project_run(self, orchestrator_config: dict) -> str:
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
        self.db.update_job(job_id, status="FAILED", error_code="DAEMON_RESTARTED", error_message="restarted")
        self.runtime.tick_once()
        self.assertEqual(self.db.get_project_step(project_run_id, "a")["status"], "failed")
        self._attach_orchestrator(project_run_id, orchestrator_config)
        return project_run_id


class DisabledOrchestratorTests(_SupervisorHarness):
    def test_no_orchestrator_config_returns_none_without_touching_anything(self):
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
        self.db.update_job(job_id, status="FAILED", error_code="DAEMON_RESTARTED", error_message="x")
        self.runtime.tick_once()

        decision = self.supervisor.on_step_failed(project_run_id, "a")
        self.assertIsNone(decision)
        self.assertEqual(self.stub_agent.calls, 0)
        self.assertEqual(self.db.get_project_step(project_run_id, "a")["status"], "failed")


class Tier0Tests(_SupervisorHarness):
    def test_tier0_hit_means_zero_agent_calls(self):
        project_run_id = self._transient_failure_project_run({"enabled": True})

        decision = self.supervisor.on_step_failed(project_run_id, "a")

        self.assertIsNotNone(decision)
        self.assertEqual(decision.strategy, "retry")
        self.assertEqual(self.stub_agent.calls, 0)
        self.assertEqual(self.db.get_project_step(project_run_id, "a")["status"], "pending")
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any(e["actor"] == "runtime" and e["kind"] == "decision" for e in events))


class Tier1Tests(_SupervisorHarness):
    def test_tier1_applies_a_valid_decision(self):
        self.stub_agent._decision = RepairDecision(
            strategy="rebind_connection", node_id="b", reason="fixing role",
            connection_overrides={"A1": "output"},
        )
        project_run_id = self._mismatched_connection_project_run({"enabled": True})

        decision = self.supervisor.on_step_failed(project_run_id, "b")

        self.assertIsNotNone(decision)
        self.assertEqual(self.stub_agent.calls, 1)
        step = self.db.get_project_step(project_run_id, "b")
        self.assertEqual(step["status"], "pending")
        self.assertEqual(json.loads(step["step_overrides_json"]), {"connection_overrides": {"A1": "output"}})
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any(e["actor"] == "orchestrator" and e["kind"] == "decision" for e in events))

    def test_tier1_repair_reaches_dispatch(self):
        self.stub_agent._decision = RepairDecision(
            strategy="rebind_connection", node_id="b", reason="fixing role",
            connection_overrides={"A1": "output"},
        )
        project_run_id = self._mismatched_connection_project_run({"enabled": True})
        self.supervisor.on_step_failed(project_run_id, "b")

        for _ in range(6):
            self.runtime.tick_once()
            status = self.db.get_project_step(project_run_id, "b")["status"]
            if status in {"running", "completed"}:
                break
        self.assertIn(self.db.get_project_step(project_run_id, "b")["status"], {"running", "completed"})


class OutOfAuthorityTests(_SupervisorHarness):
    def test_worker_not_in_available_list_is_rejected(self):
        self.stub_agent._decision = RepairDecision(
            strategy="retry_with_worker", node_id="a", reason="swap", worker="nonexistent-worker",
        )
        # Trigger a worker-unavailable scenario so evidence.available_workers is populated.
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
        self.db.update_job(job_id, requested_worker="antigravity")
        self.db.update_job(job_id, status="FAILED", error_code="WORKER_DISABLED", error_message="disabled")
        self.runtime.tick_once()
        self._attach_orchestrator(project_run_id, {"enabled": True})

        decision = self.supervisor.on_step_failed(project_run_id, "a")

        self.assertIsNone(decision)
        # Rejected, not applied: the step stays failed, no dispatch happened.
        self.assertEqual(self.db.get_project_step(project_run_id, "a")["status"], "failed")
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any("out of authority" in e["summary"] for e in events))

    def test_role_not_produced_by_upstream_is_rejected(self):
        self.stub_agent._decision = RepairDecision(
            strategy="rebind_connection", node_id="b", reason="fixing role",
            connection_overrides={"A1": "role-nothing-produced"},
        )
        project_run_id = self._mismatched_connection_project_run({"enabled": True})

        decision = self.supervisor.on_step_failed(project_run_id, "b")

        self.assertIsNone(decision)
        self.assertEqual(self.db.get_project_step(project_run_id, "b")["status"], "failed")


class BudgetTests(_SupervisorHarness):
    def test_per_node_repair_budget_exhaustion_escalates_to_terminal(self):
        self.stub_agent._decision = RepairDecision(strategy="retry", node_id="a", reason="try again")
        project_run_id = self._transient_failure_project_run(
            {"enabled": True, "max_repair_attempts_per_node": 1}
        )

        first = self.supervisor.on_step_failed(project_run_id, "a")
        self.assertIsNotNone(first)

        # Fail it again to exercise the budget a second time, via a direct status write
        # rather than ticking the runtime - orchestrator config is now attached, and a
        # tick would let ProjectRuntime's own automatic Supervisor consult (and, for this
        # Tier-0-resolvable failure, silently repair) it before this test's own explicit
        # call below runs.
        self.db.update_project_step(
            project_run_id, "a", status="failed", error_code="DAEMON_RESTARTED", error_message="x"
        )

        second = self.supervisor.on_step_failed(project_run_id, "a")
        self.assertIsNone(second)
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any("budget exhausted" in e["summary"] for e in events))

    def test_llm_call_budget_exhaustion_falls_back_without_calling_agent(self):
        # Tier 0 cannot resolve an unrecognized error code, so this always reaches Tier 1.
        project_run_id = self._transient_failure_project_run({"enabled": True, "max_llm_calls_per_run": 1})
        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        self.db.update_job(job_id, status="FAILED", error_code="SOME_UNKNOWN_ERROR", error_message="x")
        self.db.update_project_step(project_run_id, "a", status="failed", error_code="SOME_UNKNOWN_ERROR")
        # Simulate the single allowed LLM call having already been spent earlier in this Run.
        self.db.upsert_orchestrator_state(project_run_id, llm_calls_used=1)

        decision = self.supervisor.on_step_failed(project_run_id, "a")

        self.assertIsNone(decision)
        self.assertEqual(self.stub_agent.calls, 0)
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any("budget exhausted" in e["summary"] for e in events))


class RepeatedStrategyTests(_SupervisorHarness):
    def test_repeated_strategy_on_same_node_is_refused(self):
        self.stub_agent._decision = RepairDecision(strategy="retry", node_id="a", reason="try again")
        project_run_id = self._transient_failure_project_run({"enabled": True})

        first = self.supervisor.on_step_failed(project_run_id, "a")
        self.assertIsNotNone(first)

        # Direct status write, not a tick - see the comment in BudgetTests above.
        self.db.update_project_step(
            project_run_id, "a", status="failed", error_code="DAEMON_RESTARTED", error_message="x"
        )

        second = self.supervisor.on_step_failed(project_run_id, "a")
        self.assertIsNone(second)
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any("already attempted" in e["summary"] for e in events))


class AgentFailureFallbackTests(_SupervisorHarness):
    def test_agent_exception_falls_back_and_records_fallback_event(self):
        self.stub_agent._raises = TimeoutError("orchestrator worker timed out")
        project_run_id = self._mismatched_connection_project_run({"enabled": True})

        decision = self.supervisor.on_step_failed(project_run_id, "b")

        self.assertIsNone(decision)
        self.assertEqual(self.db.get_project_step(project_run_id, "b")["status"], "failed")
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any(e["kind"] == "fallback" for e in events))

    def test_run_still_reaches_terminal_state_after_agent_failure(self):
        self.stub_agent._raises = RuntimeError("boom")
        project_run_id = self._mismatched_connection_project_run({"enabled": True})
        self.supervisor.on_step_failed(project_run_id, "b")

        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed", "cancelled"}:
                break
        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "failed")


class GiveUpTests(_SupervisorHarness):
    def test_give_up_decision_records_report_and_leaves_step_failed(self):
        self.stub_agent._decision = RepairDecision(
            strategy="give_up", node_id="b", reason="missing upstream credential, not repairable"
        )
        project_run_id = self._mismatched_connection_project_run({"enabled": True})

        decision = self.supervisor.on_step_failed(project_run_id, "b")

        self.assertIsNone(decision)
        self.assertEqual(self.db.get_project_step(project_run_id, "b")["status"], "failed")
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any(e["kind"] == "report" for e in events))


if __name__ == "__main__":
    unittest.main()
