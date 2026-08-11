from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.orchestrator.narration import (
    narrate_run_completed,
    narrate_run_started,
    narrate_step_completed,
    narrate_step_dispatched,
)
from relay.orchestrator.supervisor import Supervisor
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.util import new_artifact_uid


@dataclass
class _FakeNode:
    node_id: str


@dataclass
class _FakeSpec:
    nodes: list


class NarrateRunStartedTests(unittest.TestCase):
    def test_singular_node_count(self):
        text = narrate_run_started(_FakeSpec(nodes=[_FakeNode("a")]))
        self.assertIn("1 node planned", text)

    def test_plural_node_count(self):
        text = narrate_run_started(_FakeSpec(nodes=[_FakeNode("a"), _FakeNode("b")]))
        self.assertIn("2 nodes planned", text)


class NarrateStepDispatchedTests(unittest.TestCase):
    def test_first_attempt_says_starting(self):
        self.assertIn("starting", narrate_step_dispatched("a"))

    def test_retry_says_retrying(self):
        self.assertIn("retrying", narrate_step_dispatched("a", retry=True))

    def test_includes_node_id(self):
        self.assertIn("page", narrate_step_dispatched("page"))


class NarrateStepCompletedTests(unittest.TestCase):
    def test_includes_duration_when_available(self):
        step = {"node_id": "a", "started_at": "2026-08-10T00:00:00+00:00", "completed_at": "2026-08-10T00:00:23+00:00"}
        text = narrate_step_completed(step)
        self.assertIn("a completed", text)
        self.assertIn("23s", text)

    def test_omits_duration_when_missing(self):
        step = {"node_id": "a", "started_at": None, "completed_at": None}
        text = narrate_step_completed(step)
        self.assertEqual(text, "a completed.")

    def test_minutes_and_seconds_format(self):
        step = {"node_id": "a", "started_at": "2026-08-10T00:00:00+00:00", "completed_at": "2026-08-10T00:01:05+00:00"}
        text = narrate_step_completed(step)
        self.assertIn("1m 5s", text)


class NarrateRunCompletedTests(unittest.TestCase):
    def test_singular_and_plural_counts(self):
        run = {"started_at": "2026-08-10T00:00:00+00:00", "completed_at": "2026-08-10T00:02:00+00:00"}
        text = narrate_run_completed(run, steps=[{"node_id": "a"}], final_artifacts=[{"role": "output"}])
        self.assertIn("1 step", text)
        self.assertIn("1 final artifact", text)
        self.assertIn("2m 0s", text)

    def test_plural_steps_and_artifacts(self):
        run = {"started_at": None, "completed_at": None}
        text = narrate_run_completed(
            run, steps=[{"node_id": "a"}, {"node_id": "b"}], final_artifacts=[{"role": "x"}, {"role": "y"}]
        )
        self.assertIn("2 steps", text)
        self.assertIn("2 final artifacts", text)

    def test_omits_duration_when_started_at_missing(self):
        run = {"started_at": None, "completed_at": "2026-08-10T00:02:00+00:00"}
        text = narrate_run_completed(run, steps=[], final_artifacts=[])
        self.assertNotIn(" in ", text)


class _StubAgent:
    """decide() raises by default so a test that expects Tier 0 to resolve everything
    fails loudly if the agent is unexpectedly consulted; pass a RepairDecision via
    give_up_decision to exercise the Tier 1 path deliberately instead."""

    def __init__(self, give_up_decision=None):
        self.decide_calls = 0
        self.final_report_calls = 0
        self._give_up_decision = give_up_decision

    def decide(self, evidence, state_digest):
        self.decide_calls += 1
        if self._give_up_decision is not None:
            return self._give_up_decision
        raise AssertionError("Tier 0 should have resolved this failure; the agent must not be called.")

    def final_report(self, state_digest, run_summary):
        self.final_report_calls += 1
        return "The run failed because of an unrepairable upstream error."


class _RuntimeNarrationHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.stub_agent = _StubAgent()
        self.supervisor = Supervisor(self.db, self.engine, agent_factory=lambda engine, config: self.stub_agent)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service, supervisor=self.supervisor)

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


class CleanRunNarrationTests(_RuntimeNarrationHarness):
    def test_clean_run_records_start_per_step_and_completion_notes_with_zero_agent_calls(self):
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
                "orchestrator": {"enabled": True},
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        self.runtime.tick_once()
        self._complete_step(project_run_id, "a", role="out")
        for _ in range(8):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed"}:
                break
        self._complete_step(project_run_id, "b", role="out")
        for _ in range(8):
            self.runtime.tick_once()
            if self.db.get_project_run(project_run_id)["status"] in {"completed", "failed"}:
                break

        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "completed")
        self.assertEqual(self.stub_agent.decide_calls, 0)
        self.assertEqual(self.stub_agent.final_report_calls, 0)

        events = self.db.list_project_run_events(project_run_id)
        summaries = [e["summary"] for e in events]
        self.assertTrue(any("Starting Project Run" in s for s in summaries))
        self.assertTrue(any(s.startswith("a completed") for s in summaries))
        self.assertTrue(any(s.startswith("b completed") for s in summaries))
        self.assertTrue(any(s.startswith("Run completed") for s in summaries))
        self.assertTrue(all(e["kind"] == "note" for e in events))

    def test_no_orchestrator_attached_records_no_events(self):
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
        self._complete_step(project_run_id, "a")
        self.runtime.tick_once()

        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "completed")
        self.assertEqual(self.db.list_project_run_events(project_run_id), [])


class IncidentRunClosingReportTests(_RuntimeNarrationHarness):
    def setUp(self) -> None:
        super().setUp()
        from relay.orchestrator.planner import RepairDecision

        self.stub_agent = _StubAgent(
            give_up_decision=RepairDecision(strategy="give_up", node_id="a", reason="missing credential")
        )
        self.supervisor = Supervisor(self.db, self.engine, agent_factory=lambda engine, config: self.stub_agent)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service, supervisor=self.supervisor)

    def test_unrepairable_failure_adds_exactly_one_closing_report_call(self):
        a = self._task("A")
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [],
                "orchestrator": {"enabled": True},
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        # A credential-style failure: Tier 0 has no rule for it and there is no role/worker
        # context, so plan_repair returns None with nothing for a Tier-1 agent to act on
        # either - the run finalizes as failed without ever consulting the stub agent.
        self.db.update_job(job_id, status="FAILED", error_code="MISSING_CREDENTIAL", error_message="no api key")
        self.runtime.tick_once()

        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "failed")
        self.assertEqual(self.stub_agent.final_report_calls, 1)
        events = self.db.list_project_run_events(project_run_id)
        self.assertTrue(any(e["kind"] == "report" and e["actor"] == "orchestrator" for e in events))


class NarrationHookFailureTests(_RuntimeNarrationHarness):
    def test_broken_event_write_does_not_abort_reconciliation(self):
        a = self._task("A")
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [],
                "orchestrator": {"enabled": True},
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        original = self.db.append_project_run_event

        def _boom(*args, **kwargs):
            raise RuntimeError("disk full")

        self.db.append_project_run_event = _boom
        try:
            self.runtime.tick_once()  # narrate_run_started/step_dispatched would fire here
        finally:
            self.db.append_project_run_event = original

        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        self.assertIsNotNone(job_id, "dispatch must have succeeded despite the narration hook failing")
        self._complete_step(project_run_id, "a")
        self.runtime.tick_once()

        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
