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
        wizard.set_test_result({"status": "healthy"})
        self.assertTrue(wizard.save_button.isEnabled())

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


if __name__ == "__main__":
    unittest.main()
