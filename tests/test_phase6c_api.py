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
from relay.models import JobRequest
from relay.rpc import RPCClient


class Phase6cAPITests(unittest.TestCase):
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

    def test_quality_and_semantic_routes(self):
        db = Database(self.config.path_value("database_path"))
        engine = RelayEngine(self.config, db)

        job, _ = engine.create_job(JobRequest(task="Semiconductor report", worker="codex"), queued=True)
        db.update_job(job["job_id"], status="COMPLETED", result_status="complete")

        # GET /v1/runs/{id}/quality
        q = self.client.request("GET", f"/v1/runs/{job['job_id']}/quality")
        self.assertTrue(q["ok"])
        self.assertEqual(q["quality"]["run_id"], job["job_id"])

        # POST /v1/search/semantic
        sem = self.client.request("POST", "/v1/search/semantic", {"query": "Semiconductor", "kind": "runs"})
        self.assertTrue(sem["ok"])
        self.assertTrue(sem.get("fallback", False))


if __name__ == "__main__":
    unittest.main()
