from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

from relay import __version__
from relay.agent_registry import AgentRegistry
from relay.compatibility import evaluate_compatibility, relay_home_id
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest
from relay.rpc import RPCClient


class G0ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.daemon_clients: list[tuple[RPCClient, threading.Thread]] = []

    def tearDown(self):
        for client, thread in self.daemon_clients:
            self._stop_daemon(client, thread)
        self.temp.cleanup()

    def _free_port(self) -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def _start_daemon(self) -> tuple[RelayDaemon, RPCClient, threading.Thread]:
        self.config.set("daemon_port", self._free_port())
        daemon = RelayDaemon(self.config)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        client = RPCClient(self.config)
        self.assertTrue(client.wait_until_healthy(3))
        self.daemon_clients.append((client, thread))
        return daemon, client, thread

    @staticmethod
    def _stop_daemon(client: RPCClient, thread: threading.Thread) -> None:
        if thread.is_alive():
            client.request("POST", "/shutdown")
            thread.join(timeout=10)

    def _create_job(self, task: str, *, status: str, submitted_via: str = "cli") -> str:
        job, _ = self.engine.create_job(
            JobRequest(task=task, worker="codex"), queued=status == "QUEUED", submitted_via=submitted_via
        )
        if status != "QUEUED":
            self.db.update_job(job["job_id"], status=status, completed_at="2026-07-23T10:00:00+00:00")
        return job["job_id"]

    def test_health_reports_compatibility_contract(self):
        _, client, _ = self._start_daemon()

        health = client.request("GET", "/health")

        self.assertTrue(health["ok"])
        self.assertEqual(health["api_versions"], ["v1"])
        self.assertEqual(health["api_schema_revision"], 5)
        self.assertEqual(health["min_gui_version"], "1.1.0")
        self.assertEqual(health["relay_home_id"], relay_home_id(self.home))

    def test_daemon_shutdown_joins_background_loops(self):
        daemon, client, thread = self._start_daemon()

        client.request("POST", "/shutdown")
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(daemon.scheduler.thread)
        self.assertFalse(daemon.scheduler.thread.is_alive())
        self.assertIsNotNone(daemon.schedule_loop.thread)
        self.assertFalse(daemon.schedule_loop.thread.is_alive())
        self.assertIsNotNone(daemon.maintenance.thread)
        self.assertFalse(daemon.maintenance.thread.is_alive())

    def test_current_gui_version_meets_daemon_minimum(self):
        self.assertGreaterEqual(tuple(int(part) for part in __version__.split(".")), (0, 8, 0))

    def test_jobs_api_filters_and_cursor_pagination(self):
        _, client, _ = self._start_daemon()
        self._create_job("Market research one", status="COMPLETED")
        self._create_job("Market research two", status="COMPLETED")
        self._create_job("Market research three", status="COMPLETED")
        self._create_job("Waiting task", status="QUEUED")

        query = urlencode({"bucket": "finished", "q": "Market", "limit": 2})
        first = client.request("GET", f"/v1/jobs?{query}")
        self.assertTrue(first["ok"])
        self.assertEqual(len(first["jobs"]), 2)
        self.assertTrue(first["has_more"])
        self.assertNotIn("request_json", first["jobs"][0])
        self.assertEqual(first["jobs"][0]["submitted_via"], "cli")

        second_query = urlencode({"bucket": "finished", "q": "Market", "limit": 2, "cursor": first["next_cursor"]})
        second = client.request("GET", f"/v1/jobs?{second_query}")
        self.assertEqual(len(second["jobs"]), 1)
        self.assertFalse(second["has_more"])

        waiting = client.request("GET", "/v1/jobs?bucket=waiting")
        self.assertEqual(len(waiting["jobs"]), 1)
        self.assertEqual(waiting["jobs"][0]["status"], "QUEUED")

        self._create_job("Running task", status="RUNNING")
        active = client.request("GET", "/v1/jobs?bucket=active")
        self.assertEqual({job["status"] for job in active["jobs"]}, {"QUEUED", "RUNNING"})

    def test_jobs_api_rejects_invalid_cursor(self):
        _, client, _ = self._start_daemon()

        with self.assertRaises(RelayError) as context:
            client.request("GET", "/v1/jobs?cursor=not-a-valid-cursor")
        self.assertEqual(context.exception.code, "INVALID_REQUEST")

    def test_job_metadata_and_privacy_contract(self):
        self.config.set("history_display_mode", "full")
        self.config.set("store_replayable_requests", False)
        job, _ = self.engine.create_job(
            JobRequest(task="First line\nSecond line", worker="codex"), queued=True, submitted_via="cli"
        )
        stored = self.db.get_job(job["job_id"])
        self.assertEqual(stored["title"], "First line")
        self.assertEqual(stored["task_preview"], "First line Second line")
        self.assertEqual(stored["submitted_via"], "cli")
        self.assertEqual(stored["replayable"], 0)

        self.assertTrue(self.db.request_cancel(job["job_id"]))
        stored = self.db.get_job(job["job_id"])
        self.assertEqual(stored["request_json"], "{}")
        self.assertIsNone(stored["task_text"])
        with self.assertRaises(RelayError) as context:
            self.engine.rerun(job["job_id"])
        self.assertEqual(context.exception.code, "JOB_NOT_REPLAYABLE")


class CompatibilityTests(unittest.TestCase):
    def test_compatible_health_is_normal(self):
        health = {
            "ok": True,
            "api_versions": ["v1"],
            "min_gui_version": "0.8.0",
            "relay_home_id": "home-1",
        }
        self.assertEqual(
            evaluate_compatibility(health, gui_version="0.8.0", expected_relay_home_id="home-1").mode,
            "normal",
        )

    def test_incompatible_health_is_read_only(self):
        health = {"ok": True, "api_versions": [], "min_gui_version": "0.8.0"}
        decision = evaluate_compatibility(health, gui_version="0.8.0")
        self.assertEqual(decision.mode, "read-only")

    def test_wrong_relay_home_is_disconnected(self):
        health = {
            "ok": True,
            "api_versions": ["v1"],
            "min_gui_version": "0.8.0",
            "relay_home_id": "other-home",
        }
        decision = evaluate_compatibility(health, gui_version="0.8.0", expected_relay_home_id="home-1")
        self.assertEqual(decision.mode, "disconnected")

    def test_agent_registry_keeps_builtin_definitions(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            registry = AgentRegistry(config, config.path_value("adapter_spec_root"))
            agents = {agent["agent_id"]: agent for agent in registry.list_agents()}
            self.assertEqual(set(("claude", "codex", "antigravity")), set(agents))
            self.assertTrue(agents["claude"]["builtin"])
            self.assertEqual(registry.get_adapter("codex").name, "codex")


if __name__ == "__main__":
    unittest.main()
