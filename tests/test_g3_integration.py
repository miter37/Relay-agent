from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path

from relay import __version__
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.doctor import Doctor
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.rpc import RPCClient
from relay.schedules.runtime import ScheduleRuntime
from relay.schedules.service import ScheduleService

PACKAGE = Path(__file__).resolve().parents[1]
MOCKS = PACKAGE / "mocks"


class G3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)
        self.original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(MOCKS) + os.pathsep + self.original_path
        os.environ["RELAY_TEST_PYTHON"] = sys.executable
        self.config = Config(self.home)
        self.config.init(force=True)
        self.config.set("workers.codex.command", str(MOCKS / ("codex.cmd" if os.name == "nt" else "codex")))
        self.config.set("service_isolation_acknowledged", True)
        self.config.set("soft_stall_seconds", 2)
        self.config.set("hard_stall_seconds", 4)
        self.config.set("timeout_seconds", 10)
        self.config.set("poll_interval_seconds", 0.1)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        os.environ["PATH"] = self.original_path
        os.environ.pop("RELAY_HOME", None)
        os.environ.pop("RELAY_TEST_PYTHON", None)
        self.temp.cleanup()

    def test_release_version(self):
        self.assertEqual(__version__, "1.0.0")

    def test_scheduled_job_completes_and_is_linked_to_normal_history(self):
        audit = Doctor(self.config, self.db).audit(["codex"], deep=True)
        self.assertTrue(audit["ok"], audit)

        source = self.engine.run(JobRequest(task="Generate the scheduled report", worker="codex", fallback=False))
        self.assertEqual(source["status"], "completed")
        source_id = source["job_id"]
        service = ScheduleService(self.config, self.db, self.engine)
        created = service.create_from_job(
            source_id,
            {"name": "Integration report", "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"}},
        )
        schedule_id = created["schedule_id"]
        self.db.update_schedule(schedule_id, next_run_at_utc="2026-07-23T08:00:00+00:00")

        runtime = ScheduleRuntime(self.config, self.db, self.engine)
        self.assertEqual(runtime.tick(datetime(2026, 7, 23, 9, 0, tzinfo=UTC))["queued"], 1)
        scheduled = self.db.active_jobs_for_schedule(schedule_id)[0]
        receipt = self.engine.execute_job(scheduled["job_id"])

        self.assertEqual(receipt["status"], "completed")
        job = self.db.get_job(scheduled["job_id"])
        self.assertEqual(job["status"], "COMPLETED")
        self.assertEqual(job["schedule_id"], schedule_id)
        self.assertEqual(job["caller"], "schedule")
        self.assertTrue(Path(job["output_path"]).is_file())
        self.assertNotEqual(Path(job["output_path"]).parent, Path(created["output_root"]))

    def test_daemon_health_exposes_schedule_retention_state(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.config.set("daemon_port", port)
        daemon = RelayDaemon(self.config)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        client = RPCClient(self.config)
        try:
            self.assertTrue(client.wait_until_healthy(3))
            health = client.request("GET", "/health")
            self.assertIn("schedule_retention", health)
            self.assertTrue(health["schedule_retention"]["enabled"])
        finally:
            if thread.is_alive():
                client.request("POST", "/shutdown")
                thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
