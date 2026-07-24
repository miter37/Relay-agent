from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from relay.config import Config
from relay.models import AdapterSpec
from relay.security import activate_antigravity, enabled_worker_health, full_access_settings, set_full_access_mode


class SecurityTests(unittest.TestCase):
    def test_full_access_mode_is_persisted_and_legacy_bypass_is_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()

            self.assertEqual(full_access_settings(config), {"codex": False, "claude": False, "antigravity": False})
            self.assertEqual(set_full_access_mode(config, "codex", True)["codex"], True)
            self.assertTrue(config.get("workers.codex.full_access_mode"))

            config.set("workers.codex.unsafe_yolo", True)
            self.assertTrue(full_access_settings(config)["codex"])
            self.assertFalse(set_full_access_mode(config, "codex", False)["codex"])
            self.assertFalse(config.get("workers.codex.unsafe_yolo"))

    def test_full_access_mode_rejects_unknown_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            with self.assertRaises(Exception) as context:
                set_full_access_mode(config, "unknown", True)
            self.assertEqual(context.exception.code, "INVALID_REQUEST")

    def test_enabled_worker_health_reports_all_enabled_agents(self):
        healthy_adapter = Mock()
        unhealthy_adapter = Mock()
        unhealthy_adapter.require_verified.side_effect = Exception("audit missing")
        engine = Mock()
        engine.agent_registry.list_enabled_agents.return_value = [
            {"agent_id": "codex"},
            {"agent_id": "claude"},
        ]
        engine.agent_registry.get_adapter.side_effect = [healthy_adapter, unhealthy_adapter]

        report = enabled_worker_health(engine)

        self.assertEqual(report["status"], "unhealthy")
        self.assertEqual(report["healthy"], ["codex"])
        self.assertEqual(report["unhealthy"][0]["agent_id"], "claude")

    def test_antigravity_activation_saves_verified_and_enabled_together(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            spec = AdapterSpec(
                worker="antigravity",
                executable="C:/tools/agy.exe",
                version="1.1.5",
                audited_at="2026-07-24T00:00:00+00:00",
                help_hash="help",
                shallow_ok=True,
                deep_ok=True,
                unattended_ok=True,
                output_ok=True,
                artifact_ok=True,
                status="healthy",
            )
            adapter = Mock()
            adapter.load_spec.return_value = spec
            adapter.version.return_value = "1.1.5"
            adapter.executable.return_value = "C:/tools/agy.exe"
            adapter.require_verified.return_value = spec
            engine = Mock()
            engine.agent_registry.get_adapter.return_value = adapter

            result = activate_antigravity(config, engine, isolation_acknowledged=True)

            self.assertEqual(result["antigravity"]["state"], "enabled")
            self.assertTrue(config.get("workers.antigravity.security_verified"))
            self.assertTrue(config.get("workers.antigravity.enabled"))


if __name__ == "__main__":
    unittest.main()
