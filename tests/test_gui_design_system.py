"""Offscreen regression tests for Relay's shared GUI design grammar."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.design_styles import application_palette, application_stylesheet
from relay.gui.design_tokens import COLORS, contrast_ratio, status_presentation
from relay.gui.design_widgets import EmptyState, StatusBadge


class DesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setPalette(application_palette())
        cls.app.setStyleSheet(application_stylesheet())

    def test_status_badge_uses_shared_semantic_state_and_text(self):
        badge = StatusBadge("RUNNING")

        self.assertEqual(badge.text(), "Running")
        self.assertEqual(badge.property("state"), "running")
        self.assertEqual(badge.styleSheet(), "")
        self.assertIn('QLabel#statusBadge[state="running"]', application_stylesheet())
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
        self.assertIn('QLabel#statusBadge[state="running"]', stylesheet)
        self.assertIn(COLORS["border.focus"], stylesheet)
        for surface in ("bg.canvas", "bg.surface", "bg.surfaceRaised", "bg.input"):
            self.assertGreaterEqual(contrast_ratio(COLORS["text.primary"], COLORS[surface]), 4.5)
            self.assertGreaterEqual(contrast_ratio(COLORS["text.secondary"], COLORS[surface]), 4.5)
        self.assertGreaterEqual(contrast_ratio(COLORS["text.muted"], COLORS["bg.surfaceRaised"]), 4.5)
        self.assertGreaterEqual(contrast_ratio(COLORS["text.primary"], COLORS["accent.primary"]), 4.5)

    def test_application_palette_pins_default_text_and_input_roles(self):
        palette = application_palette()
        self.assertEqual(palette.color(QPalette.WindowText).name().upper(), COLORS["text.primary"])
        self.assertEqual(palette.color(QPalette.Base).name().upper(), COLORS["bg.input"])
        self.assertEqual(palette.color(QPalette.PlaceholderText).name().upper(), COLORS["text.muted"])

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
