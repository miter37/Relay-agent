from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.agent_apps import AgentAppListView, AgentAppWizard


class G5AgentGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_wizard_builds_manifest_and_requires_passing_test_before_save(self):
        wizard = AgentAppWizard()
        wizard.name_edit.setText("OpenCode")
        wizard.id_edit.setText("opencode")
        wizard.executable_edit.setText("opencode")
        wizard.argv_edit.setPlainText("run\n{request_file}\n{result_file}")

        payload = wizard.payload()

        self.assertEqual(payload["agent_id"], "opencode")
        self.assertEqual(payload["argv"], ["run", "{request_file}", "{result_file}"])
        self.assertFalse(wizard.save_button.isEnabled())
        wizard.set_test_result({"status": "healthy"}, test_token="token", tested_payload=payload)
        self.assertTrue(wizard.save_button.isEnabled())
        wizard.executable_edit.setText("changed")
        self.assertFalse(wizard.save_button.isEnabled())

    def test_wizard_edit_round_trip_preserves_advanced_and_unknown_fields(self):
        wizard = AgentAppWizard()
        manifest = {
            "agent_id": "opencode",
            "display_name": "OpenCode",
            "description": "Agent",
            "executable": "opencode",
            "argv": ["run", "{request_file}", "{result_file}"],
            "input_mode": "request_file",
            "result_mode": "result_file",
            "result_formats": ["txt"],
            "supports_artifacts": False,
            "default_model": "fast",
            "model_list_argv": ["models", "--json"],
            "model_list_parser": "json",
            "model_list_timeout_seconds": 45,
            "model_arg": [],
            "safety": {
                "network": True,
                "workspace_write": False,
                "may_skip_permissions": True,
                "env_names": ["OPENCODE_API_KEY"],
                "future_safety": "preserved",
            },
            "future_field": {"preserved": True},
        }

        wizard.set_agent(manifest)
        payload = wizard.payload()

        self.assertEqual(payload, manifest)

    def test_agent_list_renders_needs_test_and_ready_states(self):
        view = AgentAppListView()
        view.set_agents(
            [
                {"agent_id": "opencode", "display_name": "OpenCode", "status": "needs_test", "enabled": False},
                {"agent_id": "gemini", "display_name": "Gemini CLI", "status": "ready", "enabled": True},
            ]
        )

        texts = [view.agent_list.item(index).text() for index in range(view.agent_list.count())]

        self.assertIn("Needs a test", texts[0])
        self.assertIn("Ready", texts[1])
        view._selected = view._agents["opencode"]
        view.set_agents([])
        self.assertIsNone(view._selected)
        self.assertFalse(view.delete_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
