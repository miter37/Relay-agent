from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from relay.config import Config
from relay.daemon import RelayDaemon
from relay.rpc import RPCClient


class Phase6dAPITests(unittest.TestCase):
    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("daemon_port", self._free_port())

        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()

        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(5.0))

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except Exception:
                pass
            self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_observability_routes(self):
        # Test /v1/attention
        att = self.client.request("GET", "/v1/attention")
        self.assertTrue(att["ok"])

        # Test /v1/operations/routines
        ops_r = self.client.request("GET", "/v1/operations/routines")
        self.assertTrue(ops_r["ok"])

        # Test /v1/operations/projects
        ops_p = self.client.request("GET", "/v1/operations/projects")
        self.assertTrue(ops_p["ok"])

        # Test /v1/notifications/test
        notif = self.client.request(
            "POST",
            "/v1/notifications/test",
            {
                "url": "http://127.0.0.1:8080/hook",
                "payload": {"hello": "world"},
            },
        )
        self.assertTrue(notif["ok"])


if __name__ == "__main__":
    unittest.main()
