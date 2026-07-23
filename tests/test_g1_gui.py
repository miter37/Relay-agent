from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from relay.compatibility import evaluate_compatibility
from relay.config import Config
from relay.gui.main_window import MainWindow


class G1GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "relay-home")
        self.config.init()
        self.window = MainWindow(self.config, gui_version="0.7.0", expected_home_id="home")

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def test_gui_has_read_only_sections_and_home_state(self):
        self.assertEqual(self.window.windowTitle(), "Relay-agent")
        self.assertEqual(self.window.sidebar.minimumWidth(), 0)
        self.assertIn("Relay Home:", self.window.statusBar().currentMessage())

    def test_finished_filter_and_status_rendering(self):
        self.window.current_mode = "normal"
        self.window.jobs = {
            "done": {"job_id": "done", "status": "COMPLETED", "title": "Done", "submitted_via": "cli"},
            "failed": {"job_id": "failed", "status": "FAILED", "title": "Failed", "submitted_via": "gui"},
        }
        self.window._render_jobs()
        self.assertEqual(self.window.job_list.count(), 4)
        self.window.result_filter.setCurrentText("Failed")
        self.window._render_jobs()
        self.assertEqual(self.window.job_list.count(), 3)
        self.assertIn("× Failed", self.window.job_list.item(2).text())

    def test_unsupported_schema_is_read_only(self):
        health = {"ok": True, "api_versions": ["v1"], "api_schema_revision": 99, "min_gui_version": "0.7.0"}
        self.assertEqual(evaluate_compatibility(health, gui_version="0.7.0").mode, "read-only")


if __name__ == "__main__":
    unittest.main()
