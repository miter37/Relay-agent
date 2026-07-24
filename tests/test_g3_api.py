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
from relay.models import JobRequest
from relay.rpc import RPCClient


class G3ScheduleApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.clients: list[tuple[RPCClient, threading.Thread]] = []

    def tearDown(self):
        for client, thread in self.clients:
            if thread.is_alive():
                client.request("POST", "/shutdown")
                thread.join(timeout=10)
        self.temp.cleanup()

    def _start_daemon(self) -> RPCClient:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.config.set("daemon_port", port)
        daemon = RelayDaemon(self.config)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        client = RPCClient(self.config)
        self.assertTrue(client.wait_until_healthy(3))
        daemon.scheduler.stop_event.set()
        daemon.schedule_loop.stop_event.set()
        self.clients.append((client, thread))
        return client

    def _successful_source_job(self, *, replayable: int = 1) -> str:
        job, _ = self.engine.create_job(
            JobRequest(task="Create a daily report", worker="codex", model="test-model"),
            queued=True,
        )
        self.db.update_job(
            job["job_id"],
            status="COMPLETED",
            result_status="complete",
            replayable=replayable,
            completed_at="2026-07-23T08:00:00+00:00",
        )
        return job["job_id"]

    @staticmethod
    def daily_rule() -> dict:
        return {"type": "daily", "times": ["09:00"], "timezone": "Asia/Seoul"}

    def test_create_schedule_from_successful_job_and_show_it(self):
        client = self._start_daemon()
        source_id = self._successful_source_job()

        result = client.request(
            "POST",
            f"/v1/schedules/from-job/{source_id}",
            {"name": "Daily report", "rule": self.daily_rule()},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["schedule"]["name"], "Daily report")
        schedule_id = result["schedule"]["schedule_id"]
        self.assertTrue(Path(result["schedule"]["input_root"]).is_dir())
        shown = client.request("GET", f"/v1/schedules/{schedule_id}")
        self.assertEqual(shown["schedule"]["source_job_id"], source_id)
        self.assertEqual(shown["schedule"]["rule"]["type"], "daily")

    def test_ineligible_job_returns_stable_error(self):
        client = self._start_daemon()
        source_id = self._successful_source_job(replayable=0)

        with self.assertRaises(RelayError) as context:
            client.request(
                "POST",
                f"/v1/schedules/from-job/{source_id}",
                {"name": "Not allowed", "rule": self.daily_rule()},
            )

        self.assertEqual(context.exception.code, "SCHEDULE_NOT_ELIGIBLE")

    def test_preview_pause_resume_and_delete(self):
        client = self._start_daemon()

        preview = client.request("POST", "/v1/schedules/preview", {"rule": self.daily_rule(), "limit": 2})
        self.assertEqual(len(preview["occurrences"]), 2)
        self.assertEqual(preview["occurrences"][0]["timezone"], "Asia/Seoul")

        source_id = self._successful_source_job()
        created = client.request(
            "POST",
            f"/v1/schedules/from-job/{source_id}",
            {"name": "Pause me", "rule": self.daily_rule()},
        )
        schedule_id = created["schedule"]["schedule_id"]
        paused = client.request("POST", f"/v1/schedules/{schedule_id}/pause", {})
        self.assertFalse(paused["schedule"]["enabled"])
        resumed = client.request("POST", f"/v1/schedules/{schedule_id}/resume", {})
        self.assertTrue(resumed["schedule"]["enabled"])
        deleted = client.request("DELETE", f"/v1/schedules/{schedule_id}")
        self.assertTrue(deleted["deleted"])
        self.assertIsNotNone(self.db.get_job(source_id))

    def test_run_now_creates_manual_pending_run(self):
        client = self._start_daemon()
        source_id = self._successful_source_job()
        created = client.request(
            "POST",
            f"/v1/schedules/from-job/{source_id}",
            {"name": "Run now", "rule": self.daily_rule()},
        )
        schedule_id = created["schedule"]["schedule_id"]

        result = client.request("POST", f"/v1/schedules/{schedule_id}/run-now", {})

        self.assertEqual(result["run"]["trigger_type"], "manual")
        self.assertEqual(result["run"]["status"], "PLANNED")
        self.assertEqual(self.db.list_schedule_runs(schedule_id)[0]["trigger_type"], "manual")

    def test_public_job_creation_cannot_forge_schedule_link(self):
        client = self._start_daemon()

        result = client.request("POST", "/v1/jobs", {"task": "Public task", "schedule_id": "forged"})

        self.assertIsNone(self.db.get_job(result["job_id"])["schedule_id"])

    def test_invalid_rule_is_rejected_before_snapshot_creation(self):
        client = self._start_daemon()
        source_id = self._successful_source_job()

        with self.assertRaises(RelayError) as context:
            client.request(
                "POST",
                f"/v1/schedules/from-job/{source_id}",
                {"name": "Bad", "rule": {"type": "daily", "times": ["25:00"], "timezone": "UTC"}},
            )

        self.assertEqual(context.exception.code, "SCHEDULE_RULE_INVALID")
        self.assertEqual(self.db.list_schedules(), [])


if __name__ == "__main__":
    unittest.main()
