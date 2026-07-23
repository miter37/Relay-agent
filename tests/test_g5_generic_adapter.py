from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.adapters.base import AdapterContext
from relay.adapters.generic import GenericCLIAdapter
from relay.errors import RelayError
from relay.models import AdapterSpec


class G5GenericAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.request_file = self.workspace / "request.md"
        self.request_file.write_text("Do the task", encoding="utf-8")
        self.result_file = self.workspace / "result.json"
        self.artifact_dir = self.workspace / "artifacts"
        self.artifact_dir.mkdir()
        self.ctx = AdapterContext(
            job_id="job-1",
            workspace=self.workspace,
            request_file=self.request_file,
            result_file=self.result_file,
            artifact_dir=self.artifact_dir,
            schema_file=self.workspace / "schema.json",
            result_format="json",
            profile="analysis",
            model="fast",
            config={},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_argv_renders_without_shell_reparsing(self):
        adapter = GenericCLIAdapter(
            {
                "executable": "opencode",
                "argv": ["run", "--input", "{request_file}", "--model", "{model}"],
                "input_mode": "request_file",
                "result_mode": "stdout",
                "default_model": "default",
            },
            self.workspace,
            name="opencode",
        )

        with patch.object(adapter, "executable", return_value="/usr/bin/opencode"):
            command, stdin_bytes, _env = adapter.build_command(self.ctx)

        self.assertEqual(command, ["/usr/bin/opencode", "run", "--input", str(self.request_file), "--model", "fast"])
        self.assertIsNone(stdin_bytes)

    def test_stdin_mode_returns_request_bytes_and_stdout_normalizes(self):
        adapter = GenericCLIAdapter(
            {
                "executable": "agent",
                "argv": ["run"],
                "input_mode": "stdin",
                "result_mode": "stdout",
            },
            self.workspace,
            name="agent",
        )

        with patch.object(adapter, "executable", return_value="/usr/bin/agent"):
            _command, stdin_bytes, _env = adapter.build_command(self.ctx)
        self.assertEqual(stdin_bytes, b"Do the task")

        stdout = self.workspace / "stdout.log"
        stderr = self.workspace / "stderr.log"
        stdout.write_text('{"status":"complete"}', encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        adapter.normalize_output(self.ctx, stdout, stderr)
        self.assertEqual(self.result_file.read_text(encoding="utf-8"), '{"status":"complete"}')

    def test_definition_change_invalidates_existing_deep_audit(self):
        adapter = GenericCLIAdapter(
            {
                "executable": "agent",
                "argv": ["run", "{request_file}", "{result_file}"],
                "input_mode": "request_file",
                "result_mode": "result_file",
                "_definition_hash": "new-definition",
            },
            self.workspace,
            name="agent",
        )
        spec = AdapterSpec(
            worker="agent",
            executable="/usr/bin/agent",
            version="1.0",
            audited_at="2026-07-24T00:00:00+00:00",
            help_hash=None,
            shallow_ok=True,
            deep_ok=True,
            unattended_ok=True,
            output_ok=True,
            artifact_ok=True,
            status="healthy",
            details={"definition_hash": "old-definition"},
        )
        adapter.save_spec(spec)

        with patch.object(adapter, "version", return_value="1.0"), patch.object(
            adapter, "executable", return_value="/usr/bin/agent"
        ):
            with self.assertRaises(RelayError) as context:
                adapter.require_verified()
        self.assertEqual(context.exception.code, "WORKER_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
