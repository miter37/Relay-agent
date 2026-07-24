from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.adapters.generic import GenericCLIAdapter
from relay.agent_apps import AgentAppStore
from relay.agent_registry import AgentRegistry
from relay.config import Config


class G5RegistryTests(unittest.TestCase):
    def test_manifest_agents_join_builtin_registry_and_use_generic_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            AgentAppStore(config).save(
                {
                    "schema_version": 1,
                    "agent_id": "opencode",
                    "display_name": "OpenCode",
                    "executable": "opencode",
                    "argv": ["run", "{request_file}", "{result_file}"],
                    "input_mode": "request_file",
                    "result_mode": "result_file",
                    "result_formats": ["json"],
                    "enabled": False,
                }
            )
            registry = AgentRegistry(config, config.path_value("adapter_spec_root"))

            agents = {item["agent_id"]: item for item in registry.list_agents()}

            self.assertIn("claude", agents)
            self.assertEqual(agents["opencode"]["display_name"], "OpenCode")
            self.assertFalse(agents["opencode"]["builtin"])
            self.assertIsInstance(registry.get_adapter("opencode"), GenericCLIAdapter)


if __name__ == "__main__":
    unittest.main()
