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


if __name__ == "__main__":
    unittest.main()
