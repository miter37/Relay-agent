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


if __name__ == "__main__":
    unittest.main()
