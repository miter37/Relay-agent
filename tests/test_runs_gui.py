from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.runs import RunsView


class RunsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_master_detail_list_filters_and_emits_selected_run(self):
        view = RunsView()
        view.set_runs(
            {
                "done": {"job_id": "done", "status": "COMPLETED", "title": "Weather Seoul", "submitted_via": "gui"},
                "running": {"job_id": "running", "status": "RUNNING", "title": "Weather Busan", "submitted_via": "gui"},
            },
            selected_run_id="running",
        )

        self.assertGreaterEqual(view.run_list.topLevelItemCount(), 2)
        self.assertEqual(view.selected_run_id, "running")
        view.result_filter.setCurrentText("Completed")
        self.assertEqual(view.run_list.topLevelItemCount(), 1)
        completed = view.run_list.topLevelItem(0).child(0).child(0)
        selected = []
        view.select_run_requested.connect(selected.append)
        view._on_item_clicked(completed)
        self.assertEqual(selected, ["done"])

    def test_group_by_task_toggle_lists_run_dates_with_occurrence_suffixes(self):
        view = RunsView()
        view.set_runs(
            {
                "first": {
                    "job_id": "first",
                    "task_id": "task-a",
                    "status": "COMPLETED",
                    "title": "AAA task",
                    "created_at": "2026-08-07T08:00:00+00:00",
                },
                "second": {
                    "job_id": "second",
                    "task_id": "task-a",
                    "status": "FAILED",
                    "title": "AAA task",
                    "created_at": "2026-08-07T09:00:00+00:00",
                },
                "third": {
                    "job_id": "third",
                    "task_id": "task-b",
                    "status": "COMPLETED",
                    "title": "BBB task",
                    "created_at": "2026-08-06T08:00:00+00:00",
                },
            },
            selected_run_id="second",
        )

        self.assertEqual(view.group_mode.value(), "date")
        task_button = view.group_mode.button("task")
        self.assertTrue(task_button.isCheckable())
        task_button.click()

        self.assertEqual(view.group_mode.value(), "task")
        self.assertTrue(task_button.isChecked())
        self.assertEqual(view.run_list.topLevelItemCount(), 2)
        aaa = view.run_list.topLevelItem(0)
        self.assertEqual(aaa.text(0), "AAA task · 2")
        date = view._local_date("2026-08-07T09:00:00+00:00")
        self.assertEqual(aaa.child(0).text(0), date)
        self.assertEqual(aaa.child(1).text(0), f"{date} (2)")
        self.assertEqual(view.run_list.currentItem().data(0, 256), "second")

        view.group_mode.button("date").click()
        self.assertEqual(view.group_mode.value(), "date")


if __name__ == "__main__":
    unittest.main()
