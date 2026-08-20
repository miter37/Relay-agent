from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from relay.agent_registry import AgentRegistry
from relay.config import Config
from relay.daemon import StartupAuditLoop
from relay.db import Database
from relay.errors import RelayError

PACKAGE = Path(__file__).resolve().parents[1]
MOCKS = PACKAGE / "mocks"


def mock_cli(name: str) -> str:
    return str(MOCKS / (f"{name}.cmd" if os.name == "nt" else name))


def make_config(**overrides) -> MagicMock:
    values = {"startup_audit_max_attempts": 3, "startup_audit_enabled": True, **overrides}
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: values.get(key, default)
    return config


def make_spec(*, shallow_ok: bool, deep_ok: bool) -> MagicMock:
    spec = MagicMock()
    spec.shallow_ok = shallow_ok
    spec.deep_ok = deep_ok
    return spec


class AuditWorkerUnitTests(unittest.TestCase):
    """Fast, mock-only tests of the per-worker retry/skip decision logic."""

    def setUp(self):
        self.loop = StartupAuditLoop(make_config(), MagicMock())

    def test_skips_worker_already_verified(self):
        doctor = MagicMock()
        registry = MagicMock()
        adapter = MagicMock()
        adapter.require_verified.return_value = MagicMock()
        registry.get_adapter.return_value = adapter

        self.loop._audit_worker(doctor, registry, "claude")

        adapter.require_verified.assert_called_once()
        doctor.audit_adapter.assert_not_called()

    def test_does_not_retry_when_shallow_audit_fails(self):
        doctor = MagicMock()
        registry = MagicMock()
        adapter = MagicMock()
        adapter.require_verified.side_effect = RelayError("WORKER_UNVERIFIED", "nope")
        registry.get_adapter.return_value = adapter
        doctor.audit_adapter.return_value = make_spec(shallow_ok=False, deep_ok=False)

        self.loop._audit_worker(doctor, registry, "codex")

        self.assertEqual(doctor.audit_adapter.call_count, 1)

    def test_retries_deep_audit_up_to_max_attempts(self):
        doctor = MagicMock()
        registry = MagicMock()
        adapter = MagicMock()
        adapter.require_verified.side_effect = RelayError("WORKER_UNVERIFIED", "nope")
        registry.get_adapter.return_value = adapter
        doctor.audit_adapter.return_value = make_spec(shallow_ok=True, deep_ok=False)

        self.loop._audit_worker(doctor, registry, "antigravity")

        self.assertEqual(doctor.audit_adapter.call_count, 3)

    def test_stops_retrying_once_deep_audit_succeeds(self):
        doctor = MagicMock()
        registry = MagicMock()
        adapter = MagicMock()
        adapter.require_verified.side_effect = RelayError("WORKER_UNVERIFIED", "nope")
        registry.get_adapter.return_value = adapter
        doctor.audit_adapter.side_effect = [
            make_spec(shallow_ok=True, deep_ok=False),
            make_spec(shallow_ok=True, deep_ok=True),
        ]

        self.loop._audit_worker(doctor, registry, "claude")

        self.assertEqual(doctor.audit_adapter.call_count, 2)

    def test_start_does_nothing_when_disabled_by_config(self):
        loop = StartupAuditLoop(make_config(startup_audit_enabled=False), MagicMock())
        loop.start()
        self.assertIsNone(loop.thread)

    def test_start_spawns_thread_when_enabled(self):
        loop = StartupAuditLoop(make_config(), MagicMock())
        loop.loop = MagicMock()  # avoid real registry/doctor work against a mocked config
        loop.start()
        try:
            self.assertIsNotNone(loop.thread)
        finally:
            loop.stop()


class StartupAuditLoopIntegrationTests(unittest.TestCase):
    """Exercises the real Doctor/AgentRegistry/adapter stack with mock worker CLIs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        self.original_path = os.environ.get("PATH", "")
        os.environ["RELAY_HOME"] = str(self.home)
        os.environ["PATH"] = str(MOCKS) + os.pathsep + self.original_path
        for key in list(os.environ):
            if key.startswith("RELAY_MOCK_"):
                os.environ.pop(key)
        os.environ["RELAY_TEST_PYTHON"] = sys.executable
        self.config = Config(self.home)
        self.config.init(force=True)
        self.config.set("workers.claude.command", mock_cli("claude"))
        self.config.set("workers.codex.command", mock_cli("codex"))
        self.config.set("workers.antigravity.command", mock_cli("agy"))
        self.config.set("workers.antigravity.enabled", False)
        self.config.set("soft_stall_seconds", 2)
        self.config.set("hard_stall_seconds", 4)
        self.config.set("timeout_seconds", 10)
        self.config.set("startup_audit_max_attempts", 3)
        self.db = Database(self.config.path_value("database_path"))

    def tearDown(self):
        os.environ["PATH"] = self.original_path
        self.tmp.cleanup()
        os.environ.pop("RELAY_HOME", None)
        for key in list(os.environ):
            if key.startswith("RELAY_MOCK_"):
                os.environ.pop(key)

    def _audit_count(self, worker: str) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM capability_audits WHERE worker = ? AND test_name = 'deep'",
                (worker,),
            ).fetchone()
        return row[0]

    def test_loop_skips_already_healthy_worker(self):
        adapter = AgentRegistry(self.config, self.config.path_value("adapter_spec_root")).get_adapter("claude")
        spec = adapter.shallow_audit()
        spec.deep_ok = spec.unattended_ok = spec.output_ok = spec.artifact_ok = True
        spec.status = "healthy"
        adapter.save_spec(spec)

        loop = StartupAuditLoop(self.config, self.db)
        loop.loop()

        self.assertEqual(self._audit_count("claude"), 0)

    def test_loop_retries_crashing_worker_up_to_max_attempts_then_gives_up(self):
        os.environ["RELAY_MOCK_CLAUDE_BEHAVIOR"] = "crash"

        loop = StartupAuditLoop(self.config, self.db)
        loop.loop()

        self.assertEqual(self._audit_count("claude"), 3)
        with self.assertRaises(RelayError):
            AgentRegistry(self.config, self.config.path_value("adapter_spec_root")).get_adapter(
                "claude"
            ).require_verified()

    def test_loop_verifies_healthy_worker_and_stops_after_one_attempt(self):
        loop = StartupAuditLoop(self.config, self.db)
        loop.loop()

        self.assertEqual(self._audit_count("claude"), 1)
        AgentRegistry(self.config, self.config.path_value("adapter_spec_root")).get_adapter(
            "claude"
        ).require_verified()  # does not raise

    def test_loop_excludes_disabled_worker(self):
        loop = StartupAuditLoop(self.config, self.db)
        loop.loop()

        # antigravity is disabled in setUp and must never be probed.
        self.assertEqual(self._audit_count("antigravity"), 0)


if __name__ == "__main__":
    unittest.main()
