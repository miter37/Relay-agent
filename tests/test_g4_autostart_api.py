from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from relay.config import Config
from relay.daemon import RelayDaemon
from relay.rpc import RPCClient


class G4AutoStartApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "relay-home")
        self.config.init()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.config.set("daemon_port", self.port)
        self.daemon = RelayDaemon(self.config)
        self.fake = Mock()
        self.fake.status.return_value = {
            "platform": "Windows",
            "supported": True,
            "implemented": True,
            "field_validated": True,
            "enabled": False,
            "action": "manual_start",
            "warning": None,
        }
        self.fake.enable.return_value = {**self.fake.status.return_value, "enabled": True, "action": "managed_start"}
        self.fake.disable.return_value = self.fake.status.return_value
        self.daemon.autostart_manager = self.fake
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()
        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(3))
        self.daemon.scheduler.stop_event.set()
        self.daemon.schedule_loop.stop_event.set()

    def tearDown(self):
        if self.thread.is_alive():
            self.client.request("POST", "/shutdown")
            self.thread.join(timeout=10)
        self.temp.cleanup()

    def test_autostart_status_and_toggle_are_authenticated_api_operations(self):
        status = self.client.request("GET", "/v1/autostart")
        enabled = self.client.request("PATCH", "/v1/autostart", {"enabled": True})

        self.assertTrue(status["ok"])
        self.assertFalse(status["autostart"]["enabled"])
        self.assertTrue(enabled["autostart"]["enabled"])
        self.fake.enable.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
