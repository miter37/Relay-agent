from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.rpc import RPCClient


def agent_payload():
    return {
        "agent_id": "opencode",
        "display_name": "OpenCode",
        "executable": "opencode",
        "argv": ["run", "{request_file}", "{result_file}"],
        "input_mode": "request_file",
        "result_mode": "result_file",
        "result_formats": ["json"],
    }


class G5AgentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "relay-home")
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.config.set("daemon_port", sock.getsockname()[1])
        sock.close()
        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()
        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(3))

    def tearDown(self):
        if self.thread.is_alive():
            self.client.request("POST", "/shutdown")
            self.thread.join(timeout=5)
        self.temp.cleanup()

    def test_agent_app_crud_is_authenticated_and_returns_readiness(self):
        created = self.client.request("POST", "/v1/agent-apps", agent_payload())
        self.assertFalse(created["agent"]["enabled"])
        self.assertEqual(created["agent"]["status"], "needs_test")

        listed = self.client.request("GET", "/v1/agent-apps")
        self.assertEqual([item["agent_id"] for item in listed["agent_apps"]], ["opencode"])

        updated = self.client.request("PATCH", "/v1/agent-apps/opencode", {"description": "Updated"})
        self.assertEqual(updated["agent"]["description"], "Updated")

        deleted = self.client.request("DELETE", "/v1/agent-apps/opencode")
        self.assertTrue(deleted["deleted"])

    def test_enable_requires_current_deep_test(self):
        self.client.request("POST", "/v1/agent-apps", agent_payload())

        with self.assertRaises(RelayError) as context:
            self.client.request("PATCH", "/v1/agent-apps/opencode/enabled", {"enabled": True})

        self.assertEqual(context.exception.code, "WORKER_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
