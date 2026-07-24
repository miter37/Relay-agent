from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.config import Config
from relay.gui.job_detail import JobDetailView
from relay.gui.main_window import MainWindow


class G4ScheduleGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_completed_replayable_job_has_schedule_action(self):
        view = JobDetailView()
        view.set_job(
            {
                "job_id": "job-1",
                "status": "COMPLETED",
                "actions": {"can_schedule": True, "can_rerun": True},
            }
        )

        self.assertTrue(view.schedule_button.isEnabled())

    def test_main_window_has_schedule_sidebar_and_renders_attention_state(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            window = MainWindow(config, gui_version="1.0.0", expected_home_id="home")
            window.schedules = {
                "sch-1": {
                    "schedule_id": "sch-1",
                    "name": "Daily report",
                    "enabled": 1,
                    "next_run_at_utc": "2026-07-24T00:00:00+00:00",
                    "needs_attention": True,
                }
            }

            window._render_schedules()

            self.assertTrue(hasattr(window, "schedule_list"))
            self.assertIn("Daily report", window.schedule_list.item(0).text())
            self.assertIn("×", window.schedule_list.item(0).text())
            window.close()


if __name__ == "__main__":
    unittest.main()
