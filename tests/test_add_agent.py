from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.adapters import get_adapter
from relay.adapters.base import AdapterContext
from relay.adapters.generic import (
    BUILTIN_WORKERS,
    KNOWN_PLACEHOLDERS,
    GenericCLIAdapter,
    render_command_template,
    validate_command_template,
    validate_worker_id,
)
from relay.config import Config
from relay.db import Database
from relay.errors import RelayError

PACKAGE = Path(__file__).resolve().parents[1]
MOCKS = PACKAGE / "mocks"


class WorkerIdValidationTests(unittest.TestCase):
    def test_accepts_lowercase_letters_digits_dash_underscore(self):
        validate_worker_id("opencode")
        validate_worker_id("grok-build")
        validate_worker_id("my_agent_2")

    def test_rejects_empty(self):
        with self.assertRaises(RelayError) as ctx:
            validate_worker_id("")
        self.assertEqual(ctx.exception.code, "AGENT_INVALID_NAME")

    def test_rejects_uppercase(self):
        with self.assertRaises(RelayError) as ctx:
            validate_worker_id("OpenCode")
        self.assertEqual(ctx.exception.code, "AGENT_INVALID_NAME")

    def test_rejects_spaces_and_specials(self):
        for bad in ("open code", "open.code", "open/code", "open:code", "agent!"):
            with self.subTest(bad=bad):
                with self.assertRaises(RelayError) as ctx:
                    validate_worker_id(bad)
                self.assertEqual(ctx.exception.code, "AGENT_INVALID_NAME")

    def test_rejects_builtin_ids(self):
        for builtin in BUILTIN_WORKERS:
            with self.subTest(builtin=builtin):
                with self.assertRaises(RelayError) as ctx:
                    validate_worker_id(builtin)
                self.assertEqual(ctx.exception.code, "AGENT_BUILTIN")


class CommandTemplateValidationTests(unittest.TestCase):
    def test_accepts_template_with_known_placeholders(self):
        validate_command_template("{cli} exec --prompt {request_file} --output {result_file}")

    def test_rejects_unknown_placeholder(self):
        with self.assertRaises(RelayError) as ctx:
            validate_command_template("{cli} exec --unknown {banana}")
        self.assertEqual(ctx.exception.code, "AGENT_TEMPLATE_INVALID")

    def test_rejects_empty(self):
        with self.assertRaises(RelayError) as ctx:
            validate_command_template("")
        self.assertEqual(ctx.exception.code, "AGENT_TEMPLATE_INVALID")

    def test_known_placeholders_list_is_complete(self):
        self.assertEqual(
            KNOWN_PLACEHOLDERS,
            frozenset({"cli", "request_file", "result_file", "artifact_dir", "model"}),
        )


class TemplateRenderingTests(unittest.TestCase):
    def test_substitutes_all_placeholders(self):
        rendered = render_command_template(
            "{cli} run --prompt {request_file} --out {result_file} --artifacts {artifact_dir} --model {model}",
            {
                "cli": "/usr/bin/opencode",
                "request_file": "/work/req.md",
                "result_file": "/work/result.json",
                "artifact_dir": "/work/artifacts",
                "model": "gpt-x",
            },
        )
        self.assertEqual(
            rendered,
            [
                "/usr/bin/opencode",
                "run",
                "--prompt",
                "/work/req.md",
                "--out",
                "/work/result.json",
                "--artifacts",
                "/work/artifacts",
                "--model",
                "gpt-x",
            ],
        )

    def test_quotes_paths_with_spaces(self):
        rendered = render_command_template(
            "{cli} --prompt {request_file}",
            {
                "cli": "/usr/bin/opencode",
                "request_file": "/work/has space/req.md",
                "result_file": "",
                "artifact_dir": "",
                "model": "",
            },
        )
        self.assertEqual(rendered, ["/usr/bin/opencode", "--prompt", "/work/has space/req.md"])


class GenericCLIAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)
        self.config = Config(self.home)
        self.config.init(force=True)
        self.spec_root = self.config.path_value("adapter_spec_root")
        self.worker_config = {
            "command": "opencode",
            "command_template": "{cli} exec --prompt {request_file} --output {result_file} --artifacts {artifact_dir}",
            "default_model": "gpt-x",
            "extra_args": ["--non-interactive"],
            "env_extra": {"OPENCODE_API_KEY": "secret"},
        }
        self.adapter = GenericCLIAdapter(self.worker_config, self.spec_root, name="opencode")

    def tearDown(self):
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def test_name_and_command_name(self):
        self.assertEqual(self.adapter.name, "opencode")
        self.assertEqual(self.adapter.command_name, "opencode")

    def test_build_command_substitutes_placeholders(self):
        workspace = self.home / "workspace" / "opencode" / "job1"
        (workspace / "output").mkdir(parents=True)
        (workspace / "artifacts").mkdir(parents=True)
        ctx = AdapterContext(
            job_id="job1",
            workspace=workspace,
            request_file=workspace / "request.md",
            result_file=workspace / "output" / "result.json.partial",
            artifact_dir=workspace / "artifacts",
            schema_file=workspace / "schema.json",
            result_format="json",
            profile="web-research",
            model=None,
            config=self.worker_config,
        )
        # Make sure executable is resolvable for the test
        exe = shutil.which("python") or shutil.which("python3")
        if exe is None:
            self.skipTest("no python interpreter on PATH")
        self.adapter.command_name = exe
        args, stdin_bytes, env = self.adapter.build_command(ctx)
        self.assertEqual(args[0], exe)
        self.assertIn("--prompt", args)
        self.assertIn("--output", args)
        self.assertIn("--artifacts", args)
        self.assertIn("--non-interactive", args)
        self.assertEqual(env["OPENCODE_API_KEY"], "secret")
        self.assertEqual(env["RELAY_PROVIDER_NAME"], "opencode")

    def test_detect_capabilities_returns_generic_hints(self):
        hints = self.adapter.detect_capabilities("some help text")
        self.assertIn("generic_cli_template", hints)
        self.assertEqual(hints["generic_cli_template"], True)

    def test_permission_mode_and_sandbox(self):
        self.assertEqual(self.adapter.permission_mode(), "user-defined")
        self.assertEqual(self.adapter.sandbox_mode(), "external-workspace")

    def test_normalize_output_noop_when_result_exists(self):
        workspace = self.home / "workspace" / "opencode" / "job2"
        (workspace / "output").mkdir(parents=True)
        result_file = workspace / "output" / "result.json.partial"
        result_file.write_text('{"already": "here"}', encoding="utf-8")
        ctx = AdapterContext(
            job_id="job2",
            workspace=workspace,
            request_file=workspace / "request.md",
            result_file=result_file,
            artifact_dir=workspace / "artifacts",
            schema_file=workspace / "schema.json",
            result_format="json",
            profile="web-research",
            model=None,
            config=self.worker_config,
        )
        self.adapter.normalize_output(ctx, workspace / "stdout.log", workspace / "stderr.log")
        self.assertEqual(result_file.read_text(encoding="utf-8"), '{"already": "here"}')

    def test_normalize_output_writes_text_when_format_txt(self):
        workspace = self.home / "workspace" / "opencode" / "job3"
        (workspace / "output").mkdir(parents=True)
        result_file = workspace / "output" / "result.txt.partial"
        stdout_path = workspace / "stdout.log"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("hello world", encoding="utf-8")
        ctx = AdapterContext(
            job_id="job3",
            workspace=workspace,
            request_file=workspace / "request.md",
            result_file=result_file,
            artifact_dir=workspace / "artifacts",
            schema_file=workspace / "schema.json",
            result_format="txt",
            profile="web-research",
            model=None,
            config=self.worker_config,
        )
        self.adapter.normalize_output(ctx, stdout_path, workspace / "stderr.log")
        self.assertEqual(result_file.read_text(encoding="utf-8"), "hello world")


class GetAdapterDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)
        self.config = Config(self.home)
        self.config.init(force=True)
        self.spec_root = self.config.path_value("adapter_spec_root")

    def tearDown(self):
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def test_builtin_claude_returns_claude_adapter(self):
        from relay.adapters.claude import ClaudeAdapter

        adapter = get_adapter("claude", self.config.worker("claude"), self.spec_root)
        self.assertIsInstance(adapter, ClaudeAdapter)

    def test_builtin_codex_returns_codex_adapter(self):
        from relay.adapters.codex import CodexAdapter

        adapter = get_adapter("codex", self.config.worker("codex"), self.spec_root)
        self.assertIsInstance(adapter, CodexAdapter)

    def test_builtin_antigravity_returns_antigravity_adapter(self):
        from relay.adapters.antigravity import AntigravityAdapter

        adapter = get_adapter("antigravity", self.config.worker("antigravity"), self.spec_root)
        self.assertIsInstance(adapter, AntigravityAdapter)

    def test_unknown_worker_returns_generic(self):
        worker_config = {
            "command": "opencode",
            "command_template": "{cli} exec --prompt {request_file} --output {result_file}",
        }
        adapter = get_adapter("opencode", worker_config, self.spec_root)
        self.assertIsInstance(adapter, GenericCLIAdapter)
        self.assertEqual(adapter.name, "opencode")


class AddAgentConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)
        self.config = Config(self.home)
        self.config.init(force=True)

    def tearDown(self):
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def test_apply_agent_registration_writes_block(self):
        from relay.cli import _apply_agent_registration

        _apply_agent_registration(
            self.config,
            worker_id="opencode",
            fields={
                "display_name": "OpenCode",
                "description": "open-source agent",
                "command": "opencode",
                "command_template": "{cli} exec --prompt {request_file} --output {result_file}",
                "default_model": "gpt-x",
                "require_deep_doctor": True,
                "enabled": True,
            },
        )
        reloaded = Config(self.home)
        reloaded.reload()
        block = reloaded.worker("opencode")
        self.assertEqual(block["display_name"], "OpenCode")
        self.assertEqual(block["command"], "opencode")
        self.assertEqual(block["command_template"], "{cli} exec --prompt {request_file} --output {result_file}")
        self.assertEqual(block["default_model"], "gpt-x")
        self.assertTrue(block["require_deep_doctor"])
        self.assertTrue(block["enabled"])
        self.assertEqual(block["source"], "user-registered")
        self.assertIn("registered_at", block)

    def test_apply_agent_registration_refuses_builtin_id(self):
        from relay.cli import _apply_agent_registration

        with self.assertRaises(RelayError) as ctx:
            _apply_agent_registration(self.config, worker_id="claude", fields={"command": "x"})
        self.assertEqual(ctx.exception.code, "AGENT_BUILTIN")

    def test_apply_agent_registration_refuses_duplicate(self):
        from relay.cli import _apply_agent_registration

        fields = {
            "display_name": "X",
            "command": "x",
            "command_template": "{cli} --prompt {request_file} --output {result_file}",
        }
        _apply_agent_registration(self.config, worker_id="opencode", fields=fields)
        with self.assertRaises(RelayError) as ctx:
            _apply_agent_registration(self.config, worker_id="opencode", fields=fields)
        self.assertEqual(ctx.exception.code, "AGENT_DUPLICATE")


class AddAgentWizardFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)
        self.config = Config(self.home)
        self.config.init(force=True)
        self.db = Database(self.config.path_value("database_path"))
        os.environ["PATH"] = str(MOCKS) + os.pathsep + os.environ.get("PATH", "")
        for key in list(os.environ):
            if key.startswith("RELAY_MOCK_") or key.startswith("RELAY_ADD_AGENT_"):
                os.environ.pop(key)

    def tearDown(self):
        for key in list(os.environ):
            if key.startswith("RELAY_MOCK_") or key.startswith("RELAY_ADD_AGENT_"):
                os.environ.pop(key)
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def _make_args(self, **overrides):
        from types import SimpleNamespace

        defaults = {
            "worker_id": None,
            "yes": True,
            "machine": False,
            "skip_health_check": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_non_tty_without_yes_errors(self):
        from relay.cli import _run_add_agent

        with patch("sys.stdin") as fake_stdin:
            fake_stdin.isatty.return_value = False
            args = self._make_args(yes=False)
            with self.assertRaises(RelayError) as ctx:
                _run_add_agent(args, self.config, self.db)
            self.assertEqual(ctx.exception.code, "AGENT_NOT_TTY")

    def test_yes_mode_uses_env_vars_and_runs_health(self):
        from relay.cli import _run_add_agent

        os.environ["RELAY_ADD_AGENT_ID"] = "opencode"
        os.environ["RELAY_ADD_AGENT_DISPLAY_NAME"] = "OpenCode"
        os.environ["RELAY_ADD_AGENT_COMMAND"] = "opencode"
        os.environ["RELAY_ADD_AGENT_COMMAND_TEMPLATE"] = "{cli} exec --prompt {request_file} --output {result_file}"
        os.environ["RELAY_ADD_AGENT_DEFAULT_MODEL"] = "gpt-x"
        os.environ["RELAY_ADD_AGENT_REQUIRE_DEEP"] = "true"
        os.environ["RELAY_ADD_AGENT_ENABLE"] = "true"

        try:
            with patch("relay.cli._run_health_check") as mock_health:
                mock_health.return_value = {
                    "shallow_ok": True,
                    "deep_ok": True,
                    "status": "healthy",
                    "version": "1.0.0",
                    "error": None,
                }
                args = self._make_args(yes=True)
                result = _run_add_agent(args, self.config, self.db)

            self.assertEqual(result["worker_id"], "opencode")
            self.assertEqual(result["status"], "registered")
            reloaded = Config(self.home)
            reloaded.reload()
            self.assertTrue(reloaded.worker("opencode")["enabled"])
        finally:
            for key in list(os.environ):
                if key.startswith("RELAY_ADD_AGENT_"):
                    os.environ.pop(key)

    def test_health_failure_aborts_no_persistence(self):
        from relay.cli import _run_add_agent

        os.environ["RELAY_ADD_AGENT_ID"] = "opencode"
        os.environ["RELAY_ADD_AGENT_DISPLAY_NAME"] = "OpenCode"
        os.environ["RELAY_ADD_AGENT_COMMAND"] = "opencode"
        os.environ["RELAY_ADD_AGENT_COMMAND_TEMPLATE"] = "{cli} exec --prompt {request_file} --output {result_file}"

        try:
            with patch("relay.cli._run_health_check") as mock_health:
                mock_health.return_value = {
                    "shallow_ok": False,
                    "deep_ok": False,
                    "status": "unhealthy",
                    "version": None,
                    "error": "command not found",
                }
                args = self._make_args(yes=True)
                with self.assertRaises(RelayError) as ctx:
                    _run_add_agent(args, self.config, self.db)
                self.assertEqual(ctx.exception.code, "AGENT_HEALTH_FAILED")

            reloaded = Config(self.home)
            reloaded.reload()
            self.assertNotIn("opencode", reloaded.data.get("workers", {}))
        finally:
            for key in list(os.environ):
                if key.startswith("RELAY_ADD_AGENT_"):
                    os.environ.pop(key)

    def test_skip_health_check_persists_without_audit(self):
        from relay.cli import _run_add_agent

        os.environ["RELAY_ADD_AGENT_ID"] = "opencode"
        os.environ["RELAY_ADD_AGENT_DISPLAY_NAME"] = "OpenCode"
        os.environ["RELAY_ADD_AGENT_COMMAND"] = "opencode"
        os.environ["RELAY_ADD_AGENT_COMMAND_TEMPLATE"] = "{cli} exec --prompt {request_file} --output {result_file}"
        os.environ["RELAY_ADD_AGENT_ENABLE"] = "true"

        try:
            args = self._make_args(yes=True, skip_health_check=True)
            result = _run_add_agent(args, self.config, self.db)
            self.assertEqual(result["status"], "registered")
            self.assertTrue(result["skipped_health_check"])
            reloaded = Config(self.home)
            reloaded.reload()
            self.assertTrue(reloaded.worker("opencode")["enabled"])
        finally:
            for key in list(os.environ):
                if key.startswith("RELAY_ADD_AGENT_"):
                    os.environ.pop(key)

    def test_interactive_wizard_collects_inputs(self):
        from relay.cli import _run_add_agent_wizard

        inputs = iter(
            [
                "opencode",  # worker_id
                "OpenCode",  # display name
                "opencode",  # command
                "{cli} exec --prompt {request_file} --output {result_file}",  # template
                "gpt-x",  # default model
                "",  # require_deep default Y
                "",  # enable default Y
                "n",  # decline health check
            ]
        )
        outputs: list[str] = []

        def fake_prompt(text, default=None):
            outputs.append(text)
            return next(inputs)

        def fake_yes_no(text, default=True):
            outputs.append(text)
            return next(inputs).lower() in {"y", "yes", ""} if next(inputs, "y") else default

        # The above closure got messy; use a simpler approach:
        outputs2: list[str] = []
        prompt_iter = iter(
            [
                "opencode",
                "OpenCode",
                "opencode",
                "{cli} exec --prompt {request_file} --output {result_file}",
                "gpt-x",
            ]
        )
        yes_no_iter = iter(["", "", "n"])

        def prompt2(text, default=None):
            outputs2.append(text)
            return next(prompt_iter)

        def yes_no2(text, default=True):
            outputs2.append(text)
            return next(yes_no_iter).lower() in {"y", "yes", ""} or default

        collected = _run_add_agent_wizard(prompt_fn=prompt2, yes_no_fn=yes_no2)
        fields = collected["fields"]
        self.assertEqual(collected["worker_id"], "opencode")
        self.assertEqual(fields["display_name"], "OpenCode")
        self.assertEqual(fields["command"], "opencode")
        self.assertEqual(fields["default_model"], "gpt-x")
        self.assertTrue(fields["require_deep_doctor"])
        self.assertTrue(fields["enabled"])
        self.assertIn("Worker ID", "\n".join(outputs2))
        self.assertIn("Executable", "\n".join(outputs2))
        self.assertIn("Command template", "\n".join(outputs2))


class AddAgentCliHelpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)

    def tearDown(self):
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def test_top_level_help_lists_add_agent(self):
        from relay.cli import build_parser

        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("add-agent", help_text)
        # The top-level epilog should surface quick-start commands
        self.assertIn("relay init", help_text)
        self.assertIn("add-agent", help_text)

    def test_add_agent_help_has_examples(self):
        from relay.cli import build_parser

        parser = build_parser()
        sub = parser._subparsers._group_actions[0].choices.get("add-agent")
        self.assertIsNotNone(sub)
        help_text = sub.format_help()
        self.assertIn("Examples:", help_text)
        self.assertIn("--yes", help_text)
        self.assertIn("--machine", help_text)
        self.assertGreater(len(sub.description or ""), 60)


class AgentDoctorHealthCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)
        self.config = Config(self.home)
        self.config.init(force=True)
        self.db = Database(self.config.path_value("database_path"))

    def tearDown(self):
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def test_run_health_check_uses_doctor(self):
        from relay.adapters.base import Adapter
        from relay.cli import _run_health_check

        # Patch executable() on the GenericCLIAdapter returned by get_adapter
        # so the adapter believes it is installed but the real subprocess.run
        # in shallow_audit() fails.
        original_executable = Adapter.executable

        def fake_executable(self):
            return None

        try:
            with patch.object(Adapter, "executable", fake_executable):
                fake_worker_config = {
                    "command": "opencode",
                    "command_template": "{cli} exec --prompt {request_file} --output {result_file}",
                }
                result = _run_health_check("opencode", fake_worker_config, self.config, self.db, deep=True)
        finally:
            Adapter.executable = original_executable
        self.assertFalse(result["shallow_ok"])
        self.assertIn(result["status"], {"unhealthy", "unavailable"})
        self.assertIsNotNone(result["error"])
