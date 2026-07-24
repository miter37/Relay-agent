from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.agent_apps import AgentAppStore
from relay.cli import build_parser
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest


class G5DynamicWorkerTests(unittest.TestCase):
    def test_cli_accepts_custom_worker_id_and_engine_validates_against_registry(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--worker", "opencode", "hello"])
        self.assertEqual(args.worker, "opencode")

        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            AgentAppStore(config).save(
                {
                    "schema_version": 1,
                    "agent_id": "opencode",
                    "display_name": "OpenCode",
                    "executable": "opencode",
                    "argv": ["run", "{request_file}", "{result_file}"],
                    "input_mode": "request_file",
                    "result_mode": "result_file",
                    "result_formats": ["json"],
                    "enabled": False,
                }
            )
            engine = RelayEngine(config, Database(config.path_value("database_path")))
            request = JobRequest(task="hello", worker="opencode")

            engine._resolve_request_task(request)

            self.assertEqual(request.worker, "opencode")
            self.assertEqual(engine._worker_chain({"fallback_enabled": 0}, request), ["opencode"])
            self.assertIs(engine._worker_slot("opencode"), engine._worker_slot("opencode"))


if __name__ == "__main__":
    unittest.main()
