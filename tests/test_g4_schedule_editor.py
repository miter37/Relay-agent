from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.schedule_editor import ScheduleEditorDialog


class G4ScheduleEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_daily_editor_builds_canonical_multi_time_payload(self):
        dialog = ScheduleEditorDialog(source_job_id="job-1")
        dialog.name_edit.setText("Morning report")
        dialog.type_combo.setCurrentText("Daily")
        dialog.times_edit.setText("09:00, 13:00")
        dialog.timezone_edit.setText("Asia/Seoul")
        dialog.retention_mode.setCurrentText("latest_runs")
        dialog.retention_value.setValue(5)

        payload = dialog.payload()

        self.assertEqual(payload["name"], "Morning report")
        self.assertEqual(
            payload["rule"],
            {"type": "daily", "times": ["09:00", "13:00"], "timezone": "Asia/Seoul"},
        )
        self.assertEqual(payload["retention"], {"mode": "latest_runs", "value": 5})

    def test_weekly_editor_includes_selected_iso_weekdays(self):
        dialog = ScheduleEditorDialog(source_job_id="job-1")
        dialog.type_combo.setCurrentText("Weekly")
        dialog.times_edit.setText("07:00")
        dialog.weekday_checks[0].setChecked(True)
        dialog.weekday_checks[4].setChecked(True)

        self.assertEqual(dialog.payload()["rule"]["weekdays"], [1, 5])

    def test_save_is_disabled_until_preview_succeeds(self):
        dialog = ScheduleEditorDialog(source_job_id="job-1")

        self.assertFalse(dialog.save_button.isEnabled())
        dialog.set_preview([{"local": "2026-07-24T09:00+09:00", "utc": "2026-07-24T00:00:00+00:00"}])
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.set_preview_error("Invalid timezone")
        self.assertFalse(dialog.save_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
