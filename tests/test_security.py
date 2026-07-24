from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from relay.config import Config
from relay.models import AdapterSpec
from relay.security import activate_antigravity


class SecurityTests(unittest.TestCase):
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
