from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.agent_apps import AgentAppStore
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.schedules.service import ScheduleService


class G5CustomAgentScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "relay-home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        AgentAppStore(self.config).save(
            {
                "schema_version": 1,
                "agent_id": "opencode",
                "display_name": "OpenCode",
                "executable": "opencode",
                "argv": ["run", "{request_file}", "{result_file}"],
                "input_mode": "request_file",
                "result_mode": "result_file",
                "result_formats": ["json"],
                "enabled": True,
            }
        )
        self.engine = RelayEngine(self.config, self.db)
        self.service = ScheduleService(self.config, self.db, self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def test_schedule_preserves_custom_agent_reference(self):
        job, _ = self.engine.create_job(JobRequest(task="Run OpenCode", worker="opencode"), queued=True)
        self.db.update_job(
            job["job_id"],
            status="COMPLETED",
            result_status="complete",
            replayable=1,
            request_json=json.dumps({"task": "Run OpenCode", "worker": "opencode"}),
        )

        schedule = self.service.create_from_job(
            job["job_id"],
            {
                "name": "OpenCode schedule",
                "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"},
            },
        )

        self.assertEqual(schedule["task_settings"]["worker"], "opencode")
        self.assertEqual(self.service.show(schedule["schedule_id"])["task_settings"]["worker"], "opencode")


if __name__ == "__main__":
    unittest.main()
