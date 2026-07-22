from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from relay.cleanup import CleanupManager
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.doctor import Doctor
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.rpc import RPCClient


PACKAGE = Path(__file__).resolve().parents[1]
MOCKS = PACKAGE / "mocks"


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        self.original_path = os.environ.get("PATH", "")
        os.environ["RELAY_HOME"] = str(self.home)
        os.environ["PATH"] = str(MOCKS) + os.pathsep + self.original_path
        for key in list(os.environ):
            if key.startswith("RELAY_MOCK_"):
                os.environ.pop(key)
        self.config = Config(self.home)
        self.config.init(force=True)
        self.config.set("workers.claude.command", str(MOCKS / "claude"))
        self.config.set("workers.codex.command", str(MOCKS / "codex"))
        self.config.set("workers.antigravity.command", str(MOCKS / "agy"))
        self.config.set("soft_stall_seconds", 2)
        self.config.set("hard_stall_seconds", 4)
        self.config.set("timeout_seconds", 10)
        self.config.set("poll_interval_seconds", 0.1)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        os.environ["PATH"] = self.original_path
        self.tmp.cleanup()
        os.environ.pop("RELAY_HOME", None)

    def audit_all(self, deep=True):
        if deep:
            result = Doctor(self.config, self.db).audit(["claude", "codex", "antigravity"], deep=True)
            self.assertTrue(result["ok"], result)
            return
        from relay.adapters import get_adapter
        for worker in ("claude", "codex", "antigravity"):
            adapter = get_adapter(worker, self.config.worker(worker), self.config.path_value("adapter_spec_root"))
            spec = adapter.shallow_audit()
            spec.deep_ok = spec.unattended_ok = spec.output_ok = spec.artifact_ok = True
            spec.status = "healthy"
            adapter.save_spec(spec)

    def test_daemon_runs_due_cleanup(self):
        self.audit_all(deep=False)
        result = self.engine.run(JobRequest(task="daemon cleanup", worker="codex", fallback=False))
        job_id = result["job_id"]
        workspace = self.config.path_value("workspace_root") / "codex" / job_id
        self.db.update_job(
            job_id,
            status="COMPLETED",
            completed_at="2020-01-01T00:00:00+00:00",
            updated_at="2020-01-01T00:00:00+00:00",
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.config.set("daemon_port", port)
        self.config.set("cleanup_run_on_daemon_start", True)
        daemon = RelayDaemon(self.config)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        client = RPCClient(self.config)
        self.assertTrue(client.wait_until_healthy(3))
        deadline = time.time() + 5
        while workspace.exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertFalse(workspace.exists())
        self.assertTrue(Path(result["result_path"]).exists())
        client.request("POST", "/shutdown")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_deep_doctor_and_antigravity_opt_in(self):
        self.audit_all()
        self.assertFalse(self.config.get("workers.antigravity.enabled"))

    def test_sync_json_delivery(self):
        self.audit_all(deep=False)
        result = self.engine.run(JobRequest(task="research something", worker="claude", fallback=False))
        self.assertTrue(result["ok"], result)
        output = Path(result["result_path"])
        self.assertTrue(output.is_file())
        value = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(value["status"], "complete")
        self.assertTrue((Path(result["artifact_path"]) / "manifest.json").is_file())

    def test_fallback_to_codex(self):
        self.audit_all(deep=False)
        os.environ["RELAY_MOCK_CLAUDE_BEHAVIOR"] = "crash"
        result = self.engine.run(JobRequest(task="fallback test", worker="auto", fallback=True))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["worker"], "codex")
        self.assertEqual(result["attempted_workers"], ["claude", "codex"])

    def test_exact_dedup(self):
        self.audit_all(deep=False)
        req = JobRequest(task="same task", worker="codex", request_id="telegram-1", fallback=False)
        first = self.engine.run(req)
        second = self.engine.run(req)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second.get("deduplicated"))

    def test_daemon_submit(self):
        self.audit_all(deep=False)
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
        submitted = client.request("POST", "/submit", JobRequest(task="async", worker="codex", fallback=False).to_dict())
        job_id = submitted["job_id"]
        deadline = time.time() + 10
        result = None
        while time.time() < deadline:
            result = client.request("GET", f"/result/{job_id}")
            if result.get("status") in {"completed", "partial", "failed"}:
                break
            time.sleep(0.1)
        self.assertTrue(result and result.get("ok"), result)
        client.request("POST", "/shutdown")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "Relay daemon thread did not stop")

    def test_cleanup_retention_policy(self):
        self.audit_all(deep=False)
        result = self.engine.run(JobRequest(task="cleanup test", worker="codex", fallback=False))
        job_id = result["job_id"]
        workspace = self.config.path_value("workspace_root") / "codex" / job_id
        self.assertTrue(workspace.exists())
        self.db.update_job(
            job_id,
            status="COMPLETED",
            completed_at="2020-01-01T00:00:00+00:00",
            updated_at="2020-01-01T00:00:00+00:00",
        )
        report = CleanupManager(self.config, self.db).run()
        self.assertTrue(report["ok"], report)
        self.assertFalse(workspace.exists())
        self.assertTrue(Path(result["result_path"]).exists())
        self.assertTrue(Path(result["artifact_path"]).exists())

    def test_cleanup_preserves_recent_and_active_jobs(self):
        self.audit_all(deep=False)
        result = self.engine.run(JobRequest(task="recent cleanup test", worker="codex", fallback=False))
        job_id = result["job_id"]
        workspace = self.config.path_value("workspace_root") / "codex" / job_id
        report = CleanupManager(self.config, self.db).run()
        self.assertTrue(report["ok"], report)
        self.assertTrue(workspace.exists())

    def test_cleanup_status_and_due_state(self):
        manager = CleanupManager(self.config, self.db)
        self.config.set("cleanup_enabled", True)
        self.config.set("cleanup_run_on_daemon_start", True)
        self.assertTrue(manager.due())
        manager.run()
        self.assertFalse(manager.due())
        status = manager.status()
        self.assertIsNotNone(status["last_run"])


if __name__ == "__main__":
    unittest.main()
