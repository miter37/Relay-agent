from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.agent_apps import AgentAppStore
from relay.config import Config
from relay.errors import RelayError


def manifest(**overrides):
    value = {
        "schema_version": 1,
        "agent_id": "opencode",
        "display_name": "OpenCode",
        "description": "OpenCode CLI",
        "executable": "opencode",
        "argv": ["run", "--input", "{request_file}", "--output", "{result_file}"],
        "input_mode": "request_file",
        "result_mode": "result_file",
        "result_formats": ["json", "txt"],
        "supports_artifacts": True,
        "default_model": "",
        "model_list_argv": [],
        "model_list_parser": "lines",
        "model_arg": ["--model", "{model}"],
        "safety": {
            "network": True,
            "workspace_write": True,
            "may_skip_permissions": False,
            "env_names": ["OPENCODE_API_KEY"],
        },
        "enabled": False,
    }
    value.update(overrides)
    return value


class G5ManifestStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "relay-home")
        self.config.init()
        self.store = AgentAppStore(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_save_round_trips_manifest_and_writes_canonical_json(self):
        saved = self.store.save(manifest())

        self.assertEqual(saved["agent_id"], "opencode")
        self.assertEqual(self.store.get("opencode")["argv"][2], "{request_file}")
        raw = (self.store.root / "opencode.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(raw), saved)

    def test_validation_rejects_shell_execution_forms(self):
        for argv in (["run", "&&", "other"], ["run", "$(touch", "bad)"], ["run", "`whoami`"]):
            with self.assertRaises(RelayError) as context:
                self.store.save(manifest(argv=argv))
            self.assertEqual(context.exception.code, "AGENT_TEMPLATE_INVALID")

    def test_delete_moves_manifest_to_recoverable_trash(self):
        self.store.save(manifest())

        self.assertTrue(self.store.delete("opencode"))
        self.assertIsNone(self.store.get("opencode"))
        self.assertEqual(len(list(self.store.trash.glob("opencode-*.json"))), 1)

    def test_legacy_worker_is_imported_without_removing_original_config(self):
        self.config.set(
            "workers.opencode",
            {
                "enabled": True,
                "display_name": "OpenCode",
                "command": "opencode",
                "command_template": "{cli} run --input {request_file} --output {result_file}",
                "default_model": "fast",
            },
        )

        imported = self.store.import_legacy()

        self.assertEqual([item["agent_id"] for item in imported], ["opencode"])
        self.assertTrue(self.store.get("opencode")["enabled"] is False)
        self.assertEqual(
            self.config.worker("opencode")["command_template"],
            "{cli} run --input {request_file} --output {result_file}",
        )


if __name__ == "__main__":
    unittest.main()
