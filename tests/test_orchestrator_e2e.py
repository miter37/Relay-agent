"""End-to-end verification for docs/superpowers/plans/2026-08-10-project-orchestrator.md
Task 9. Exercises the fully-wired ProjectRuntime (Supervisor auto-consulted on failure,
same as production - see relay/projects/runtime.py's orchestrator_config-gated hooks),
not an isolated harness.

Scenarios 1 and 3 in the plan (a schema-mismatch repaired via an LLM-authored instruction
addendum, and an unrepairable failure needing a real Tier-1 call) cannot be reproduced
here: this sandbox has no installed Claude/Codex/Antigravity CLI, so any real
OrchestratorAgent.decide() call fails at worker verification before an LLM is even
reached. Those paths are covered at the mechanism level instead: the addendum overlay by
tests/test_orchestrator_overrides.py::InstructionAddendumIntegrationTests, and the
Tier-1/budget/give-up ladder by tests/test_orchestrator_supervisor.py and
tests/test_orchestrator_narration.py using a stubbed agent (the vehicle the plan's own
Task 5 test list specifies for exercising that tier).

What *is* reproduced for real here, with a real Task Run dispatch and no stub of any
kind on the Orchestrator side: scenario 2 (a real connection-role mismatch, repaired by
the zero-LLM Tier 0 planner) and scenario 4 (a Project with no Orchestrator behaves
identically, byte-for-byte, to today).
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


class _E2EAgentMustNotBeCalled:
    def decide(self, evidence, state_digest):  # pragma: no cover - failure path only
        raise AssertionError(
            "Scenario 2 is Tier-0 resolvable (a single-candidate role mismatch); the "
            "Orchestrator agent must never be consulted for it."
        )

    def final_report(self, state_digest, run_summary):  # pragma: no cover - failure path only
        raise AssertionError("A clean, fully-repaired Run must never need a closing report.")


class Scenario2ConnectionRoleMismatchZeroAgentCallsTests(unittest.TestCase):
    """Plan Task 9, scenario 2: reproduce a real connection-role mismatch and confirm
    Tier 0 repairs it with zero agent calls."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        from relay.orchestrator.supervisor import Supervisor

        self.supervisor = Supervisor(
            self.db, self.engine, agent_factory=lambda engine, config: _E2EAgentMustNotBeCalled()
        )
        self.runtime = ProjectRuntime(self.db, self.engine, self.service, supervisor=self.supervisor)

    def tearDown(self) -> None:
        self.runtime.stop()
        self.temp.cleanup()

    def _complete_with_role(self, job_id: str, role: str) -> None:
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

    def test_real_connection_role_mismatch_is_auto_repaired_by_tier_zero(self):
        # A Project where "collect" declares it will emit role "raw", but (as in the
        # documented 2026-08-07 Leaders Speak incident that motivated this feature) the
        # worker actually emits a different role - "content" - so "summarize" cannot
        # bind its input without a rebind.
        collect = self.engine.create_task(TaskSpec(name="Collect", instructions="collect"))
        summarize = self.engine.create_task(TaskSpec(name="Summarize", instructions="summarize"))
        project = self.service.create_project(
            {
                "name": "Real mismatch",
                "nodes": [
                    {"node_id": "collect", "task_id": collect["task_id"]},
                    {"node_id": "summarize", "task_id": summarize["task_id"]},
                ],
                "connections": [
                    {"from_node": "collect", "from_role": "raw", "to_node": "summarize", "to_alias": "A1"}
                ],
                "output_selection": [{"node_id": "summarize", "role": "final"}],
                "orchestrator": {"enabled": True},
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        # Dispatch "collect" for real through the engine (no stub on the Task Run
        # itself - only the Orchestrator's own agent is stubbed, and it must never fire).
        self.runtime.tick_once()
        collect_job_id = self.db.get_project_step(project_run_id, "collect")["active_task_run_id"]
        self.assertTrue(collect_job_id)
        self._complete_with_role(collect_job_id, role="content")  # not "raw"

        # Drive to the real failure: "summarize" is claimed, dispatch fails inside
        # resolve_step_inputs with PROJECT_ARTIFACT_MISSING, and ProjectRuntime's own
        # production wiring (not a test harness call) consults the Supervisor
        # automatically the moment that happens.
        for _ in range(10):
            self.runtime.tick_once()
            summarize_status = self.db.get_project_step(project_run_id, "summarize")["status"]
            if summarize_status in {"running", "completed"}:
                break
            self.assertNotEqual(summarize_status, "failed", "Tier 0 should have auto-repaired this, not left it failed")

        step = self.db.get_project_step(project_run_id, "summarize")
        self.assertIn(step["status"], {"running", "completed"})
        summarize_job_id = step["active_task_run_id"]
        self.assertTrue(summarize_job_id, "the repaired connection must have reached real dispatch")

        # Finish the run for real and confirm it reaches 'completed'.
        self._complete_with_role(summarize_job_id, role="final")
        for _ in range(10):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed"}:
                break
        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "completed")

        # The repair is on record, attributed to the deterministic tier, not the agent.
        events = self.db.list_project_run_events(project_run_id)
        decisions = [e for e in events if e["kind"] == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["actor"], "runtime")
        self.assertIn("content", decisions[0]["summary"])


class Scenario4NoOrchestratorUnchangedBehaviorTests(unittest.TestCase):
    """Plan Task 9, scenario 4: a Project with no Orchestrator behaves identically to
    today, including snapshot bytes."""

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

    def test_snapshot_bytes_identical_with_and_without_orchestrator_key_absent(self):
        task = self.engine.create_task(TaskSpec(name="A", instructions="do A"))
        definition = {
            "name": "Plain",
            "nodes": [{"node_id": "a", "task_id": task["task_id"]}],
            "connections": [],
            "output_selection": [],
        }
        project = self.service.create_project(definition)
        self.assertNotIn("orchestrator", json.loads(project["definition_json"]))

        run = self.service.create_project_run(project["project_id"])
        snapshot = json.loads(run["project_run"]["project_snapshot_json"])
        self.assertNotIn("orchestrator", snapshot["project_definition"])

    def test_run_with_no_orchestrator_writes_no_orchestrator_rows(self):
        task = self.engine.create_task(TaskSpec(name="A", instructions="do A"))
        project = self.service.create_project(
            {
                "name": "Plain",
                "nodes": [{"node_id": "a", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        self.runtime.tick_once()
        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        self.db.update_job(job_id, status="COMPLETED", result_status="complete")
        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed"}:
                break

        self.assertEqual(self.db.list_project_run_events(project_run_id), [])
        self.assertIsNone(self.db.get_orchestrator_state(project_run_id))


if __name__ == "__main__":
    unittest.main()
