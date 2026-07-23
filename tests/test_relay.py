from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.cleanup import CleanupManager
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.delivery import atomic_deliver_directory
from relay.doctor import Doctor
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest
from relay.rpc import RPCClient
from relay.util import entrypoint_command
from relay.validation import materialize_artifact_payloads, validate_json_result

PACKAGE = Path(__file__).resolve().parents[1]
MOCKS = PACKAGE / "mocks"


def mock_cli(name: str) -> str:
    """Path to a bundled mock CLI.

    The mocks ship twice: as POSIX shell scripts (``mocks/claude``) and as
    Windows batch wrappers (``mocks/claude.cmd``). Windows cannot execute a
    ``#!`` script, so pick the wrapper there.
    """
    return str(MOCKS / (f"{name}.cmd" if os.name == "nt" else name))


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
        # The Windows wrappers run the mock under this interpreter, so the
        # subprocess always matches the Python running the tests. Deliberately
        # not named RELAY_MOCK_* -- that prefix is cleared just above.
        os.environ["RELAY_TEST_PYTHON"] = sys.executable
        self.config = Config(self.home)
        self.config.init(force=True)
        self.config.set("workers.claude.command", mock_cli("claude"))
        self.config.set("workers.codex.command", mock_cli("codex"))
        self.config.set("workers.antigravity.command", mock_cli("agy"))
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

    def test_request_id_conflict_is_rejected(self):
        first, _ = self.engine.create_job(
            JobRequest(task="first", worker="codex", request_id="same-request"), queued=True
        )
        with self.assertRaises(RelayError) as context:
            self.engine.create_job(JobRequest(task="different", worker="codex", request_id="same-request"), queued=True)
        self.assertEqual(getattr(context.exception, "code", None), "REQUEST_ID_CONFLICT")
        self.assertEqual(self.db.get_job(first["job_id"])["status"], "QUEUED")

    def test_concurrent_request_id_submission_reuses_one_job(self):
        requests = [JobRequest(task="same", worker="codex", request_id="concurrent") for _ in range(12)]
        results = []
        errors = []
        lock = threading.Lock()

        def submit(request):
            try:
                job, reused = self.engine.create_job(request, queued=True)
                with lock:
                    results.append((job["job_id"], reused))
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=submit, args=(request,)) for request in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors, errors)
        self.assertEqual({job_id for job_id, _ in results}, {results[0][0]})

    def test_queued_cancel_is_final(self):
        job, _ = self.engine.create_job(JobRequest(task="cancel me", worker="codex"), queued=True)
        self.assertTrue(self.db.request_cancel(job["job_id"]))
        stored = self.db.get_job(job["job_id"])
        self.assertEqual(stored["status"], "CANCELLED")
        self.assertEqual(self.engine.receipt(job["job_id"])["status"], "cancelled")

    def test_codex_command_keeps_workspace_sandbox(self):
        from relay.adapters.base import AdapterContext
        from relay.adapters.codex import CodexAdapter

        worker_config = self.config.worker("codex")
        worker_config["command"] = mock_cli("codex")
        adapter = CodexAdapter(worker_config, self.config.path_value("adapter_spec_root"))
        workspace = self.home / "workspace" / "codex-command"
        workspace.mkdir(parents=True)
        ctx = AdapterContext(
            job_id="command-test",
            workspace=workspace,
            request_file=workspace / "request.md",
            result_file=workspace / "result.json.partial",
            artifact_dir=workspace / "artifacts",
            schema_file=workspace / "schema.json",
            result_format="json",
            profile="web-research",
            model=None,
            config=worker_config,
        )
        command, prompt, _ = adapter.build_command(ctx)
        self.assertIn("--sandbox", command)
        self.assertIn("workspace-write", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn(b"Do not attempt direct filesystem writes for artifacts", prompt)
        self.assertIn(b"valid artifact payload counts as completed work", prompt)

    def test_claude_command_strips_unsupported_schema_uri(self):
        from relay.adapters.base import AdapterContext
        from relay.adapters.claude import ClaudeAdapter
        from relay.request_builder import STANDARD_JSON_SCHEMA

        worker_config = self.config.worker("claude")
        worker_config["command"] = mock_cli("claude")
        adapter = ClaudeAdapter(worker_config, self.config.path_value("adapter_spec_root"))
        workspace = self.home / "workspace" / "claude-command"
        workspace.mkdir(parents=True)
        schema_file = workspace / "schema.json"
        schema_file.write_text(json.dumps(STANDARD_JSON_SCHEMA), encoding="utf-8")
        ctx = AdapterContext(
            job_id="claude-command-test",
            workspace=workspace,
            request_file=workspace / "request.md",
            result_file=workspace / "result.json.partial",
            artifact_dir=workspace / "artifacts",
            schema_file=schema_file,
            result_format="json",
            profile="analysis-only",
            model=None,
            config=worker_config,
        )

        command, _, _ = adapter.build_command(ctx)
        schema = json.loads(command[command.index("--json-schema") + 1])
        self.assertNotIn("$schema", schema)
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], STANDARD_JSON_SCHEMA["required"])

    def test_codex_output_schema_uses_supported_keywords(self):
        from relay.request_builder import STANDARD_JSON_SCHEMA

        self.assertNotIn("oneOf", json.dumps(STANDARD_JSON_SCHEMA))
        self.assertEqual(STANDARD_JSON_SCHEMA["properties"]["sources"]["items"], {"type": "string"})
        artifact_schema = STANDARD_JSON_SCHEMA["properties"]["artifacts"]["items"]
        self.assertEqual(artifact_schema["type"], "object")
        self.assertEqual(
            set(artifact_schema["required"]),
            {"relative_path", "description", "encoding", "content"},
        )

    def test_model_catalog_verify_does_not_reuse_unverified_cache(self):
        from relay.model_catalog import DiscoveredModel, ModelCatalog
        from relay.model_discovery import get_model_catalog

        class CatalogAdapter:
            name = "claude"

            def __init__(self):
                self.calls = []

            def version(self):
                return "test-cli 1.0"

            def discover_models(self, *, refresh, include_hidden, verify):
                self.calls.append((refresh, include_hidden, verify))
                availability = "verified" if verify else "configured"
                return ModelCatalog(
                    worker=self.name,
                    cli_version=self.version(),
                    status="ok",
                    source="test",
                    account_scoped=False,
                    authoritative=False,
                    models=[DiscoveredModel("test-model", "Test", "test-model", availability)],
                )

        adapter = CatalogAdapter()
        get_model_catalog(self.config, adapter, refresh=True, verify=False)
        catalog = get_model_catalog(self.config, adapter, refresh=False, verify=True)

        self.assertEqual(adapter.calls, [(True, False, False), (False, False, True)])
        self.assertEqual(catalog.models[0].availability, "verified")

    def test_materializes_declared_utf8_artifact_payload(self):
        artifact_dir = self.home / "payload-artifacts"
        value = {
            "artifacts": [
                {
                    "relative_path": "reports/result.txt",
                    "description": "test artifact",
                    "encoding": "utf-8",
                    "content": "CODEX_RELAY_ARTIFACT_OK",
                }
            ]
        }
        materialized = materialize_artifact_payloads(value, artifact_dir, 10, 1024)
        self.assertEqual(materialized, ["reports/result.txt"])
        self.assertEqual(
            (artifact_dir / "reports" / "result.txt").read_text(encoding="utf-8"),
            "CODEX_RELAY_ARTIFACT_OK",
        )

    def test_normalizes_artifacts_prefix_in_payload_path(self):
        artifact_dir = self.home / "payload-artifacts"
        value = {
            "artifacts": [
                {
                    "relative_path": "artifacts/result.txt",
                    "description": "prefixed artifact",
                    "encoding": "utf-8",
                    "content": "normalized",
                }
            ]
        }
        materialized = materialize_artifact_payloads(value, artifact_dir, 10, 1024)
        self.assertEqual(materialized, ["result.txt"])
        self.assertEqual(value["artifacts"][0]["relative_path"], "result.txt")
        self.assertTrue((artifact_dir / "result.txt").is_file())
        self.assertFalse((artifact_dir / "artifacts" / "result.txt").exists())

    def test_rejects_artifact_payload_path_escape(self):
        artifact_dir = self.home / "payload-artifacts"
        value = {
            "artifacts": [
                {
                    "relative_path": "../escaped.txt",
                    "description": "escape attempt",
                    "encoding": "utf-8",
                    "content": "blocked",
                }
            ]
        }
        with self.assertRaises(RelayError) as context:
            materialize_artifact_payloads(value, artifact_dir, 10, 1024)
        self.assertEqual(context.exception.code, "ARTIFACT_PATH_VIOLATION")
        self.assertFalse((self.home / "escaped.txt").exists())

    def test_workspace_override_is_used(self):
        request = JobRequest(task="workspace", worker="codex", workspace=str(self.home / "custom-workspace"))
        paths = self.engine._prepare_workspace("workspace-job", "codex", request)
        self.assertEqual(paths["workspace"], (self.home / "custom-workspace" / "codex" / "workspace-job").resolve())

    def test_module_entrypoint_starts_daemon_as_module(self):
        with patch.dict(os.environ, {"RELAY_ENTRYPOINT": ""}):
            with patch("sys.argv", [str(PACKAGE / "relay" / "__main__.py")]):
                command = entrypoint_command(["daemon", "serve"])
        self.assertEqual(command[1:3], ["-m", "relay"])

    def test_source_daemon_start_exports_relay_import_path(self):
        from relay.cli import _start_daemon

        with patch("relay.cli.subprocess.Popen") as popen:
            with patch("relay.cli.RPCClient.health", return_value=False):
                with patch("relay.cli.RPCClient.wait_until_healthy", return_value=True):
                    _start_daemon(self.config)
        env = popen.call_args.kwargs["env"]
        self.assertIn(str(PACKAGE), env.get("PYTHONPATH", "").split(os.pathsep))
        self.assertTrue(popen.call_args.kwargs["stdout"].closed)

    def test_rpc_preserves_daemon_error_code_from_http_response(self):
        import io
        import urllib.error

        error = urllib.error.HTTPError(
            "http://127.0.0.1/submit",
            400,
            "bad request",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "ok": False,
                        "error_code": "REQUEST_ID_CONFLICT",
                        "error_message": "request id already belongs to another task",
                    }
                ).encode("utf-8")
            ),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RelayError) as context:
                RPCClient(self.config).request("POST", "/submit", {})
        self.assertEqual(context.exception.code, "REQUEST_ID_CONFLICT")
        self.assertEqual(context.exception.message, "request id already belongs to another task")

    def test_missing_attachment_returns_stable_error(self):
        self.audit_all(deep=False)
        result = self.engine.run(
            JobRequest(
                task="missing attachment",
                worker="codex",
                fallback=False,
                attachments=[str(self.home / "missing.txt")],
            )
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "ATTACHMENT_NOT_FOUND")

    def test_cli_run_returns_nonzero_for_failed_receipt(self):
        from relay.cli import main

        self.config.set("workers.antigravity.enabled", False)
        with patch.dict(os.environ, {"RELAY_HOME": str(self.home)}):
            code = main(["run", "disabled worker", "--worker", "antigravity", "--no-fallback", "--machine"])
        self.assertEqual(code, 2)

    def test_cli_wait_returns_nonzero_for_cancelled_receipt(self):
        from relay.cli import main

        job, _ = self.engine.create_job(JobRequest(task="cancelled", worker="codex"), queued=True)
        self.db.update_job(job["job_id"], status="CANCELLED", error_code="CANCELLED")
        with patch.dict(os.environ, {"RELAY_HOME": str(self.home)}):
            code = main(["wait", job["job_id"], "--timeout", "1", "--machine"])
        self.assertEqual(code, 2)

    def test_directory_delivery_failure_preserves_existing_target(self):
        source = self.home / "source-artifacts"
        target = self.home / "final-artifacts"
        source.mkdir()
        target.mkdir()
        (target / "old.txt").write_text("keep", encoding="utf-8")
        with patch("relay.delivery.shutil.copytree", side_effect=OSError("copy failed")):
            with self.assertRaises(RelayError) as context:
                atomic_deliver_directory(source, target, overwrite=True)
        self.assertEqual(getattr(context.exception, "code", None), "DELIVERY_FAILED")
        self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "keep")

    def test_json_validation_rejects_non_string_collection_items(self):
        result = self.home / "invalid-result.json"
        result.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "status": "complete",
                    "answer": "ok",
                    "sources": [1],
                    "uncertainties": [],
                    "missing_items": [],
                    "artifacts": [],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(RelayError) as context:
            validate_json_result(result, 1024 * 1024)
        self.assertEqual(getattr(context.exception, "code", None), "SCHEMA_MISMATCH")

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
        submitted = client.request(
            "POST", "/submit", JobRequest(task="async", worker="codex", fallback=False).to_dict()
        )
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
