from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.schedule_detail import ScheduleDetailView


class G4ScheduleDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_active_schedule_exposes_pause_run_and_history(self):
        view = ScheduleDetailView()
        view.set_schedule(
            {
                "schedule_id": "sch-1",
                "name": "Daily report",
                "enabled": 1,
                "source_job_id": "job-1",
                "next_run_at_utc": "2026-07-24T00:00:00+00:00",
                "task_settings": {"worker": "codex"},
            },
            [{"run_id": "run-1", "status": "COMPLETED", "job_id": "job-2", "output_available": True}],
        )

        self.assertTrue(view.pause_button.isEnabled())
        self.assertTrue(view.run_now_button.isEnabled())
        self.assertFalse(view.resume_button.isEnabled())
        self.assertIn("run-1", view.run_history.toPlainText())
        self.assertIn("worker", view.task_settings.toPlainText())

    def test_paused_schedule_exposes_resume(self):
        view = ScheduleDetailView()
        view.set_schedule({"schedule_id": "sch-2", "name": "Paused", "enabled": 0}, [])

        self.assertFalse(view.pause_button.isEnabled())
        self.assertTrue(view.resume_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
