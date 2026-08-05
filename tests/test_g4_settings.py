from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.settings import SettingsView


class G4SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_autostart_status_and_toggle_are_visible(self):
        view = SettingsView()
        view.set_autostart_status(
            {"enabled": False, "platform": "Linux", "field_validated": False, "manual_start": True}
        )

        self.assertIn("Linux", view.autostart_status.text())
        self.assertIn("manual", view.autostart_status.text().lower())
        self.assertEqual(view.autostart_button.text(), "Enable auto-start")

    def test_antigravity_status_and_activation_button_are_visible(self):
        view = SettingsView()
        view.set_antigravity_status(
            {
                "state": "ready",
                "version": "1.1.5",
                "audit": {"status": "healthy", "deep_ok": True},
            }
        )

        self.assertIn("Ready", view.antigravity_status.text())
        self.assertTrue(view.antigravity_button.isEnabled())

    def test_antigravity_activation_pending_disables_button(self):
        view = SettingsView()
        view.set_antigravity_pending(True)

        self.assertFalse(view.antigravity_button.isEnabled())
        self.assertIn("Checking", view.antigravity_button.text())

    def test_worker_deep_doctor_status_and_pending_state(self):
        view = SettingsView()
        view.set_worker_health(
            {
                "healthy": ["codex"],
                "unhealthy": [{"agent_id": "claude", "code": "AUTH_REQUIRED"}],
            }
        )
        self.assertEqual(view.doctor_status_labels["codex"].text(), "Deep doctor passed")
        self.assertIn("AUTH_REQUIRED", view.doctor_status_labels["claude"].text())
        view.set_doctor_pending("antigravity", True)
        self.assertFalse(view.doctor_buttons["antigravity"].isEnabled())
        self.assertIn("Running", view.doctor_buttons["antigravity"].text())
        view.set_doctor_result("antigravity", {"ok": True, "workers": [{"worker": "antigravity", "status": "healthy"}]})
        self.assertTrue(view.doctor_buttons["antigravity"].isEnabled())
        self.assertEqual(view.doctor_status_labels["antigravity"].text(), "Deep doctor passed")


if __name__ == "__main__":
    unittest.main()
