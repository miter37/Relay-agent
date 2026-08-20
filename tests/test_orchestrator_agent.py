from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.errors import RelayError
from relay.orchestrator.agent import OrchestratorAgent, render_prompt
from relay.orchestrator.planner import Evidence


class _StubEngine:
    def __init__(self, receipt: dict | None = None, *, raise_error: Exception | None = None):
        self._receipt = receipt
        self._raise_error = raise_error
        self.last_request = None
        self.last_submitted_via = None

    def run(self, request, submitted_via=None, **kwargs):
        self.last_request = request
        self.last_submitted_via = submitted_via
        if self._raise_error:
            raise self._raise_error
        return self._receipt


class RenderPromptTests(unittest.TestCase):
    def test_prompt_includes_node_id_and_schema(self):
        evidence = Evidence(node_id="page", error_code="PROJECT_ARTIFACT_MISSING", error_message="missing")
        prompt = render_prompt(evidence, state_digest="")
        self.assertIn("page", prompt)
        self.assertIn("PROJECT_ARTIFACT_MISSING", prompt)
        self.assertIn('"action"', prompt)

    def test_prompt_never_includes_full_instructions_or_artifact_content(self):
        evidence = Evidence(node_id="a", error_code="X", error_message="short", log_tail=["line1", "line2"])
        prompt = render_prompt(evidence, state_digest="")
        # Only the bounded log tail may appear, never a claim of full task instructions.
        self.assertIn("line1", prompt)
        self.assertNotIn("task_snapshot", prompt)


class OrchestratorAgentDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _result_file(self, payload: dict) -> str:
        path = Path(self.temp.name) / "result.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_decide_dispatches_via_engine_run_with_orchestrator_submitted_via(self):
        result_path = self._result_file({"action": "retry", "node_id": "a", "reason": "transient"})
        engine = _StubEngine({"ok": True, "status": "completed", "result_path": result_path})
        agent = OrchestratorAgent(engine, worker="claude")
        evidence = Evidence(node_id="a", error_code="DAEMON_RESTARTED", error_message="x")

        decision = agent.decide(evidence, state_digest="")

        self.assertEqual(engine.last_submitted_via, "orchestrator")
        self.assertEqual(engine.last_request.worker, "claude")
        self.assertEqual(decision.strategy, "retry")
        self.assertEqual(decision.node_id, "a")

    def test_decide_reads_decision_from_content_envelope(self):
        result_path = self._result_file(
            {"ok": True, "content": {"action": "give_up", "node_id": "a", "reason": "not repairable"}}
        )
        engine = _StubEngine({"ok": True, "status": "completed", "result_path": result_path})
        agent = OrchestratorAgent(engine)
        evidence = Evidence(node_id="a", error_code="X", error_message="x")

        decision = agent.decide(evidence, state_digest="")
        self.assertEqual(decision.strategy, "give_up")

    def test_review_reads_decision_from_relay_json_answer_envelope(self):
        result_path = self._result_file(
            {
                "schema_version": "1.0",
                "status": "complete",
                "answer": json.dumps(
                    {"decision": "rerun", "reason": "missing sources", "comment": "Add source URLs."}
                ),
                "sources": [],
                "uncertainties": [],
                "missing_items": [],
                "artifacts": [],
            }
        )
        engine = _StubEngine({"ok": True, "status": "completed", "result_path": result_path})
        agent = OrchestratorAgent(engine)

        decision = agent.review(node_id="a", guidelines="Check sources.", evidence={"result": "report"})

        self.assertEqual(decision["decision"], "rerun")
        self.assertEqual(decision["comment"], "Add source URLs.")

    def test_decide_raises_on_incomplete_task_run(self):
        engine = _StubEngine({"ok": False, "status": "failed", "error_code": "ALL_WORKERS_FAILED"})
        agent = OrchestratorAgent(engine)
        evidence = Evidence(node_id="a", error_code="X", error_message="x")
        with self.assertRaises(RelayError):
            agent.decide(evidence, state_digest="")

    def test_decide_raises_on_malformed_json_result(self):
        path = Path(self.temp.name) / "bad.json"
        path.write_text("not json at all", encoding="utf-8")
        engine = _StubEngine({"ok": True, "status": "completed", "result_path": str(path)})
        agent = OrchestratorAgent(engine)
        evidence = Evidence(node_id="a", error_code="X", error_message="x")
        with self.assertRaises(RelayError):
            agent.decide(evidence, state_digest="")

    def test_decide_raises_on_node_id_mismatch(self):
        result_path = self._result_file({"action": "retry", "node_id": "wrong-node", "reason": "x"})
        engine = _StubEngine({"ok": True, "status": "completed", "result_path": result_path})
        agent = OrchestratorAgent(engine)
        evidence = Evidence(node_id="a", error_code="X", error_message="x")
        with self.assertRaises(RelayError):
            agent.decide(evidence, state_digest="")

    def test_decide_propagates_engine_failure(self):
        engine = _StubEngine(raise_error=RuntimeError("worker CLI not installed"))
        agent = OrchestratorAgent(engine)
        evidence = Evidence(node_id="a", error_code="X", error_message="x")
        with self.assertRaises(RuntimeError):
            agent.decide(evidence, state_digest="")


if __name__ == "__main__":
    unittest.main()
