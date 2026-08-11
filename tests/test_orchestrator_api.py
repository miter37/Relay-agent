from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.api import project_run_orchestrator
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import TaskSpec


class _ApiHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _solo_project_run(self, orchestrator: dict | None = None) -> str:
        task = self.engine.create_task(TaskSpec(name="A", instructions="do A"))
        definition = {
            "name": "Solo",
            "nodes": [{"node_id": "a", "task_id": task["task_id"]}],
            "connections": [],
            "output_selection": [],
        }
        if orchestrator is not None:
            definition["orchestrator"] = orchestrator
        project = self.engine.project_service.create_project(definition)
        run = self.engine.project_service.create_project_run(project["project_id"])
        return run["project_run_id"]


class ProjectRunOrchestratorEndpointTests(_ApiHarness):
    def test_no_orchestrator_returns_disabled_with_empty_stream(self):
        project_run_id = self._solo_project_run()

        response = project_run_orchestrator(self.engine, project_run_id)

        self.assertTrue(response["ok"])
        self.assertFalse(response["enabled"])
        self.assertEqual(response["events"], [])
        self.assertIsNone(response["budget"])
        self.assertEqual(response["promotion_proposals"], [])

    def test_enabled_orchestrator_returns_events_and_budget(self):
        project_run_id = self._solo_project_run({"enabled": True, "max_llm_calls_per_run": 4})
        self.db.append_project_run_event(
            project_run_id,
            node_id="a",
            kind="decision",
            actor="orchestrator",
            summary="Retrying a.",
            detail={"strategy": "retry"},
        )
        self.db.upsert_orchestrator_state(project_run_id, llm_calls_used=2)

        response = project_run_orchestrator(self.engine, project_run_id)

        self.assertTrue(response["enabled"])
        self.assertEqual(len(response["events"]), 1)
        self.assertEqual(response["events"][0]["summary"], "Retrying a.")
        self.assertEqual(response["events"][0]["detail"], {"strategy": "retry"})
        self.assertEqual(response["budget"]["llm_calls_used"], 2)
        self.assertEqual(response["budget"]["max_llm_calls_per_run"], 4)
        self.assertEqual(response["budget"]["repair_attempts_used"], 1)

    def test_events_are_returned_in_seq_order(self):
        project_run_id = self._solo_project_run({"enabled": True})
        self.db.append_project_run_event(project_run_id, node_id=None, kind="note", actor="runtime", summary="Started.")
        self.db.append_project_run_event(
            project_run_id, node_id="a", kind="decision", actor="orchestrator", summary="Fixed."
        )

        response = project_run_orchestrator(self.engine, project_run_id)

        self.assertEqual([e["summary"] for e in response["events"]], ["Started.", "Fixed."])

    def test_unknown_project_run_raises(self):
        with self.assertRaises(RelayError):
            project_run_orchestrator(self.engine, "does-not-exist")


class ProjectRunReceiptOrchestratorFieldsTests(_ApiHarness):
    def test_receipt_step_carries_step_overrides_and_orchestrator_summary(self):
        project_run_id = self._solo_project_run({"enabled": True})
        self.db.update_project_step(project_run_id, "a", step_overrides_json=json.dumps({"worker_override": "codex"}))
        self.db.append_project_run_event(
            project_run_id, node_id="a", kind="decision", actor="orchestrator", summary="Swapped worker to codex."
        )

        receipt = self.engine.project_service.project_run_receipt(project_run_id)

        step = next(s for s in receipt["steps"] if s["node_id"] == "a")
        self.assertEqual(step["step_overrides"], {"worker_override": "codex"})
        self.assertEqual(step["orchestrator_summary"], "Swapped worker to codex.")

    def test_receipt_step_without_events_has_none_summary(self):
        project_run_id = self._solo_project_run()

        receipt = self.engine.project_service.project_run_receipt(project_run_id)

        step = next(s for s in receipt["steps"] if s["node_id"] == "a")
        self.assertIsNone(step["orchestrator_summary"])
        self.assertEqual(step["step_overrides"], {})


class _StubClient:
    def __init__(self, response=None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self._response = response if response is not None else {"ok": True}

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        return self._response


class ProjectRunCliRoutingTests(unittest.TestCase):
    def test_orchestrator_command_requests_the_orchestrator_endpoint(self):
        from relay.cli import _project_run_cli_request

        client = _StubClient()
        args = argparse.Namespace(project_run_command="orchestrator", project_run_id="run-1")
        with patch("relay.cli._ensure_daemon", return_value=client):
            _project_run_cli_request(args, config=None)

        self.assertEqual(client.calls, [("GET", "/v1/project-runs/run-1/orchestrator", None)])


class ProjectCliOrchestratorRoutingTests(unittest.TestCase):
    def test_orchestrator_show_reads_and_extracts_the_orchestrator_field(self):
        from relay.cli import _project_cli_request

        definition = {"name": "P", "orchestrator": {"enabled": True, "worker": "claude"}}
        client = _StubClient({"project": {"definition_json": json.dumps(definition)}})
        args = argparse.Namespace(project_command="orchestrator-show", project_id="proj-1")
        with patch("relay.cli._ensure_daemon", return_value=client):
            result = _project_cli_request(args, config=None)

        self.assertEqual(result["orchestrator"], {"enabled": True, "worker": "claude"})
        self.assertEqual(client.calls, [("GET", "/v1/projects/proj-1", None)])

    def test_orchestrator_set_merges_flags_and_posts_full_definition(self):
        from relay.cli import _project_cli_request

        existing_definition = {"name": "P", "nodes": [{"node_id": "a", "task_id": "t1"}]}
        client = _StubClient({"project": {"definition_json": json.dumps(existing_definition)}})
        args = argparse.Namespace(
            project_command="orchestrator-set",
            project_id="proj-1",
            enabled="true",
            worker="claude",
            model="claude-opus-4-6",
            profile=None,
            max_repair_attempts_per_node=3,
            max_repair_attempts_per_run=None,
            max_llm_calls_per_run=None,
        )
        with patch("relay.cli._ensure_daemon", return_value=client):
            _project_cli_request(args, config=None)

        get_call, post_call = client.calls
        self.assertEqual(get_call, ("GET", "/v1/projects/proj-1", None))
        method, path, payload = post_call
        self.assertEqual((method, path), ("POST", "/v1/projects/proj-1"))
        self.assertEqual(
            payload["orchestrator"],
            {
                "enabled": True,
                "worker": "claude",
                "model": "claude-opus-4-6",
                "max_repair_attempts_per_node": 3,
            },
        )
        self.assertEqual(payload["nodes"], existing_definition["nodes"])

    def test_orchestrator_set_disabled_preserves_prior_config(self):
        from relay.cli import _project_cli_request

        existing_definition = {
            "name": "P",
            "orchestrator": {"enabled": True, "worker": "claude", "max_llm_calls_per_run": 8},
        }
        client = _StubClient({"project": {"definition_json": json.dumps(existing_definition)}})
        args = argparse.Namespace(
            project_command="orchestrator-set",
            project_id="proj-1",
            enabled="false",
            worker=None,
            model=None,
            profile=None,
            max_repair_attempts_per_node=None,
            max_repair_attempts_per_run=None,
            max_llm_calls_per_run=None,
        )
        with patch("relay.cli._ensure_daemon", return_value=client):
            _project_cli_request(args, config=None)

        _, post_call = client.calls
        self.assertEqual(
            post_call[2]["orchestrator"],
            {"enabled": False, "worker": "claude", "max_llm_calls_per_run": 8},
        )


if __name__ == "__main__":
    unittest.main()
