from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from relay.agent_apps import AgentAppService
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine


def payload():
    return {
        "agent_id": "opencode",
        "display_name": "OpenCode",
        "executable": "opencode",
        "argv": ["run", "{request_file}", "{result_file}"],
        "input_mode": "request_file",
        "result_mode": "result_file",
        "result_formats": ["json"],
        "supports_artifacts": True,
        "safety": {"network": True, "workspace_write": True, "env_names": []},
    }


class G5AgentAppServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "relay-home")
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = AgentAppService(self.config, self.db, self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_always_starts_disabled_and_update_runtime_invalidates(self):
        created = self.service.create({**payload(), "enabled": True})
        self.assertFalse(created["enabled"])

        updated = self.service.update("opencode", {"argv": ["run", "--safe", "{request_file}", "{result_file}"], "enabled": True})

        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["status"], "needs_test")

    def test_enable_requires_a_passing_deep_test(self):
        self.service.create(payload())
        adapter = Mock()
        self.engine.agent_registry.get_adapter = Mock(return_value=adapter)

        enabled = self.service.set_enabled("opencode", True)

        adapter.require_verified.assert_called_once_with()
        self.assertTrue(enabled["enabled"])

    def test_delete_is_blocked_when_an_enabled_schedule_references_agent(self):
        job_id = "job-source"
        self.db.create_job(
            {
                "job_id": job_id,
                "caller": "human",
                "submitted_via": "cli",
                "task_hash": "hash",
                "format": "json",
                "profile": "default",
                "output_path": str(self.config.home / "result.json"),
                "artifact_path": str(self.config.home / "artifacts"),
                "status": "COMPLETED",
                "requested_worker": "opencode",
                "request_json": json.dumps({"task": "scheduled", "worker": "opencode"}),
                "replayable": 1,
            }
        )
        self.db.create_schedule(
            {
                "schedule_id": "schedule-1",
                "name": "Daily",
                "source_job_id": job_id,
                "rule_json": json.dumps({"type": "daily", "times": ["09:00"], "timezone": "UTC"}),
                "timezone": "UTC",
                "enabled": 1,
                "overlap_policy": "skip",
                "missed_policy": "skip",
                "missed_grace_seconds": 43200,
                "input_root": str(self.config.home / "schedule-inputs" / "schedule-1"),
                "output_root": str(self.config.home / "schedule-outputs" / "schedule-1"),
                "retention_json": "{}",
                "next_run_at_utc": "2026-07-25T00:00:00+00:00",
                "last_occurrence_key": None,
            }
        )
        self.service.create(payload())

        with self.assertRaisesRegex(Exception, "AGENT_IN_USE"):
            self.service.delete("opencode")


if __name__ == "__main__":
    unittest.main()
