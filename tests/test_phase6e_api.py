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
from relay.models import TaskSpec
from relay.rpc import RPCClient


class Phase6eAPITests(unittest.TestCase):
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

    def test_export_import_and_receipt_schema_routes(self):
        db = Database(self.config.path_value("database_path"))
        engine = RelayEngine(self.config, db)
        engine.create_task(TaskSpec(name="API Task", instructions="inst", task_id="t-api-1"))

        # GET /v1/receipt-schema
        rs = self.client.request("GET", "/v1/receipt-schema")
        self.assertTrue(rs["ok"])
        self.assertEqual(rs["receipt_schema_version"], 1)

        # POST /v1/export
        export_out = Path(self.temp.name) / "exported.zip"
        exp = self.client.request("POST", "/v1/export", {"out_path": str(export_out)})
        self.assertTrue(exp["ok"])
        self.assertTrue(Path(exp["archive_path"]).is_file())

        # POST /v1/import
        imp = self.client.request("POST", "/v1/import", {"archive_path": str(export_out), "conflict": "skip"})
        self.assertTrue(imp["ok"])


if __name__ == "__main__":
    unittest.main()
