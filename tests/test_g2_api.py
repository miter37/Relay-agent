from __future__ import annotations

import json
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


class G2JobApiTests(unittest.TestCase):
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
            if thread.is_alive():
                client.request("POST", "/shutdown")
                thread.join(timeout=10)
        self.temp.cleanup()

    def _free_port(self) -> int:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def _start_daemon(self) -> RPCClient:
        self.config.set("daemon_port", self._free_port())
        daemon = RelayDaemon(self.config)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        client = RPCClient(self.config)
        self.assertTrue(client.wait_until_healthy(3))
        daemon.scheduler.stop_event.set()
        self.daemon_clients.append((client, thread))
        return client

    def test_gui_create_forces_human_gui_identity(self):
        client = self._start_daemon()

        result = client.request(
            "POST",
            "/v1/jobs",
            {
                "task": "Research the G2 API",
                "worker": "codex",
                "caller": "service",
                "submitted_via": "schedule",
                "model": "gpt-test",
                "fallback_agents": ["claude"],
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        job = self.db.get_job(result["job_id"])
        self.assertEqual(job["caller"], "human")
        self.assertEqual(job["submitted_via"], "gui")
        request = json.loads(job["request_json"])
        self.assertEqual(request["fallback_agents"], ["claude"])
        self.assertEqual(request["model"], "gpt-test")

    def test_gui_can_cancel_queued_job(self):
        client = self._start_daemon()
        created = client.request("POST", "/v1/jobs", {"task": "Cancel me", "worker": "codex"})

        result = client.request("POST", f"/v1/jobs/{created['job_id']}/cancel", {})

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(self.db.get_job(created["job_id"])["status"], "CANCELLED")

    def test_cancel_accepts_validation_and_delivery_states(self):
        job, _ = self.engine.create_job(JobRequest(task="Cancel during delivery", worker="codex"), queued=True)
        self.db.update_job(job["job_id"], status="VALIDATING")

        result = self.engine.cancel(job["job_id"])

        self.assertEqual(result["status"], "CANCEL_REQUESTED")
        self.assertEqual(self.db.get_job(job["job_id"])["status"], "CANCEL_REQUESTED")

    def test_gui_rerun_queues_new_job_with_fresh_output_paths(self):
        client = self._start_daemon()
        original, _ = self.engine.create_job(
            JobRequest(
                task="Run again",
                worker="codex",
                output_path=str(self.home / "old-result.json"),
                artifact_path=str(self.home / "old-artifacts"),
            ),
            queued=True,
            submitted_via="cli",
        )
        self.db.update_job(original["job_id"], status="COMPLETED", completed_at="2026-07-23T12:00:00+00:00")

        result = client.request("POST", f"/v1/jobs/{original['job_id']}/rerun", {})

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "queued")
        self.assertNotEqual(result["job_id"], original["job_id"])
        rerun = self.db.get_job(result["job_id"])
        self.assertEqual(rerun["caller"], "human")
        self.assertEqual(rerun["submitted_via"], "gui")
        self.assertEqual(rerun["status"], "QUEUED")
        self.assertNotEqual(rerun["output_path"], original["output_path"])
        self.assertNotEqual(rerun["artifact_path"], original["artifact_path"])

    def test_non_replayable_job_cannot_be_rerun(self):
        client = self._start_daemon()
        self.config.set("store_replayable_requests", False)
        original, _ = self.engine.create_job(JobRequest(task="Private task", worker="codex"), queued=True)
        self.db.update_job(original["job_id"], status="COMPLETED", completed_at="2026-07-23T12:00:00+00:00")

        with self.assertRaises(RelayError) as context:
            client.request("POST", f"/v1/jobs/{original['job_id']}/rerun", {})

        self.assertEqual(context.exception.code, "JOB_NOT_REPLAYABLE")

    def test_explicit_fallback_agents_override_global_order(self):
        request = JobRequest(task="fallback", worker="claude", fallback=True, fallback_agents=["codex"])
        job, _ = self.engine.create_job(request, queued=True)

        self.assertEqual(self.engine._worker_chain(job, request), ["claude", "codex"])

    def test_agents_endpoint_exposes_builtin_agent_choices(self):
        client = self._start_daemon()

        result = client.request("GET", "/v1/agents")

        agents = {agent["agent_id"]: agent for agent in result["agents"]}
        self.assertEqual(set(("claude", "codex", "antigravity")), set(agents))
        self.assertTrue(agents["codex"]["enabled"])
        self.assertIn("default_model", agents["codex"])

    def test_job_detail_exposes_actions_without_raw_request_json(self):
        client = self._start_daemon()
        output = self.home / "results" / "inspect.json"
        artifact_dir = self.home / "artifacts" / "inspect"
        output.parent.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True)
        output.write_text("{}", encoding="utf-8")
        created = client.request(
            "POST",
            "/v1/jobs",
            {
                "task": "Inspect details",
                "worker": "codex",
                "model": "gpt-test",
                "output_path": str(output),
                "artifact_path": str(artifact_dir),
            },
        )
        self.db.update_job(created["job_id"], status="COMPLETED", completed_at="2026-07-23T12:00:00+00:00")

        detail = client.request("GET", f"/v1/jobs/{created['job_id']}")

        self.assertNotIn("request_json", detail)
        self.assertIn("request", detail)
        self.assertEqual(detail["request"]["model"], "gpt-test")
        self.assertTrue(detail["actions"]["can_rerun"])
        self.assertFalse(detail["actions"]["can_cancel"])
        self.assertTrue(detail["actions"]["can_open_result"])
        self.assertTrue(detail["actions"]["can_open_folder"])

    def test_job_logs_can_filter_error_lines_without_breaking_offsets(self):
        client = self._start_daemon()
        job, _ = self.engine.create_job(JobRequest(task="Filter logs", worker="codex"), queued=True)
        log_path = self.home / "logs" / "stderr.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("info one\nERROR failed\ninfo two\n", encoding="utf-8")
        attempt_id = self.db.create_attempt(job["job_id"], "codex", stderr_path=str(log_path))

        result = client.request(
            "GET",
            f"/v1/jobs/{job['job_id']}/logs?attempt_id={attempt_id}&stream=stderr&errors_only=1",
        )

        self.assertEqual(result["text"], "ERROR failed")
        self.assertEqual(result["next_offset"], log_path.stat().st_size)

    def test_result_artifacts_and_events_are_read_separately(self):
        client = self._start_daemon()
        output = self.home / "results" / "result.json"
        artifact_dir = self.home / "artifacts" / "job"
        artifact_dir.mkdir(parents=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"status":"completed","answer":"hello"}', encoding="utf-8")
        job, _ = self.engine.create_job(
            JobRequest(task="Read result", worker="codex", output_path=str(output), artifact_path=str(artifact_dir)),
            queued=True,
        )
        self.db.update_job(job["job_id"], status="COMPLETED", completed_at="2026-07-23T12:00:00+00:00")
        artifact = artifact_dir / "summary.txt"
        artifact.write_text("summary", encoding="utf-8")
        self.db.add_artifact(
            job["job_id"],
            relative_path="summary.txt",
            final_path=str(artifact),
            mime_type="text/plain",
            size=7,
            sha256="test-sha",
        )
        self.db.add_event(job["job_id"], "JOB_COMPLETED", {"source": "test"})

        result = client.request("GET", f"/v1/jobs/{job['job_id']}/result")
        artifacts = client.request("GET", f"/v1/jobs/{job['job_id']}/artifacts")
        events = client.request("GET", f"/v1/jobs/{job['job_id']}/events")

        self.assertEqual(result["data"]["answer"], "hello")
        self.assertEqual(artifacts["artifacts"][0]["relative_path"], "summary.txt")
        self.assertEqual(events["events"][-1]["event_type"], "JOB_COMPLETED")

    def test_logs_support_tail_and_incremental_offsets(self):
        client = self._start_daemon()
        job, _ = self.engine.create_job(JobRequest(task="Read logs", worker="codex"), queued=True)
        log_path = self.home / "logs" / "stdout.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_bytes(b"0123456789abcdef")
        attempt_id = self.db.create_attempt(job["job_id"], "codex", stdout_path=str(log_path))

        first = client.request("GET", f"/v1/jobs/{job['job_id']}/logs?attempt_id={attempt_id}&stream=stdout&limit=8")
        log_path.write_bytes(b"0123456789abcdefXYZ")
        second = client.request(
            "GET",
            f"/v1/jobs/{job['job_id']}/logs?attempt_id={attempt_id}&stream=stdout&offset={first['next_offset']}&limit=8",
        )

        self.assertEqual(first["text"], "89abcdef")
        self.assertEqual(second["text"], "XYZ")
        self.assertEqual(second["next_offset"], 19)


if __name__ == "__main__":
    unittest.main()
