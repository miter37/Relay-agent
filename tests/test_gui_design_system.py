"""Offscreen regression tests for Relay's shared GUI design grammar."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.design_styles import application_stylesheet
from relay.gui.design_tokens import COLORS, status_presentation
from relay.gui.design_widgets import EmptyState, StatusBadge


class DesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_status_badge_uses_shared_semantic_state_and_text(self):
        badge = StatusBadge("RUNNING")

        self.assertEqual(badge.text(), "Running")
        self.assertEqual(badge.property("state"), "running")
        self.assertIn(COLORS["state.info"], badge.styleSheet())
        self.assertEqual(status_presentation("unknown").label, "Unavailable")

    def test_empty_state_explains_next_safe_action(self):
        state = EmptyState("No Task Runs yet", "Run a Task to create the first traceable execution.", "New Task")

        self.assertEqual(state.objectName(), "emptyState")
        self.assertEqual(state.action_button.text(), "New Task")
        self.assertIn("traceable execution", state.description_label.text())

    def test_application_stylesheet_covers_shell_focus_and_status_variants(self):
        stylesheet = application_stylesheet()

        self.assertIn("#sidebarNav", stylesheet)
        self.assertIn("#topBar", stylesheet)
        self.assertIn("QPushButton#primaryAction", stylesheet)
        self.assertIn('QWidget#statusBadge[state="running"]', stylesheet)
        self.assertIn(COLORS["border.focus"], stylesheet)

    def test_main_window_marks_the_shared_shell_and_primary_action(self):
        from relay.compatibility import relay_home_id
        from relay.config import Config
        from relay.gui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as temp:
            config = Config(Path(temp) / "home")
            config.init()
            window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
            try:
                self.assertEqual(window.top_bar.objectName(), "topBar")
                self.assertEqual(window.sidebar.objectName(), "sidebarNav")
                self.assertEqual(window.new_task_button.objectName(), "primaryAction")
                self.assertEqual(window.tasks_button.objectName(), "sidebarButton")
                window._show_tasks()
                self.assertTrue(window.tasks_button.isChecked())
                self.assertFalse(window.runs_button.isChecked())
                self.assertEqual(window.page_title_label.text(), "Tasks")
                window._show_runs()
                self.assertTrue(window.runs_button.isChecked())
                self.assertEqual(window.page_title_label.text(), "Runs")
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
