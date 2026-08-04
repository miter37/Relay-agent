"""Phase 3 Tasks GUI widget tests.

These tests instantiate widgets under ``QT_QPA_PLATFORM=offscreen`` so
they exercise the production rendering and signal wiring without needing
a desktop session.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.tasks import (
    SaveRunAsTaskDialog,
    TaskDetailView,
    TaskEditorDialog,
    TaskListView,
    TaskRunDialog,
    TasksView,
)


class TasksWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_task_list_renders_filters_and_emits_select(self):
        view = TaskListView()
        view.set_tasks(
            [
                {"task_id": "alpha", "name": "Weekly HBM", "version": 2, "default_worker": "codex"},
                {"task_id": "beta", "name": "Daily Report", "version": 1, "default_worker": "auto"},
            ]
        )
        self.assertEqual(view.list_widget.count(), 2)
        view.search_edit.setText("report")
        self.assertEqual(view.list_widget.count(), 1)
        view.search_edit.setText("")
        self.assertEqual(view.list_widget.count(), 2)
        seen = []
        view.select_task_requested.connect(lambda tid: seen.append(tid))
        view.list_widget.setCurrentRow(0)
        view._item_activated(view.list_widget.currentItem())
        view.list_widget.setCurrentRow(1)
        view._item_activated(view.list_widget.currentItem())
        self.assertIn("alpha", seen)
        self.assertIn("beta", seen)

    def test_task_detail_renders_definition_and_runs(self):
        view = TaskDetailView()
        view.set_task(
            {
                "task_id": "alpha",
                "name": "Weekly HBM",
                "version": 2,
                "default_worker": "codex",
                "fallback_enabled": True,
                "timeout_seconds": 60,
                "profile": "web-research",
                "result_format": "json",
                "instructions": "Write the HBM report",
            },
            [
                {
                    "job_id": "job-1",
                    "status": "COMPLETED",
                    "completed_at": "2026-08-04T01:00:00+09:00",
                    "actual_worker": "codex",
                },
            ],
        )
        self.assertEqual(view.title_label.text(), "Weekly HBM")
        self.assertIn("v2", view.status_label.text())
        self.assertTrue(view.run_button.isEnabled())
        self.assertTrue(view.delete_button.isEnabled())
        view.clear()
        self.assertEqual(view.title_label.text(), "Task")
        self.assertFalse(view.run_button.isEnabled())

    def test_task_editor_payload_validates_input_schema_json(self):
        dialog = TaskEditorDialog()
        dialog.name_edit.setText("Weekly Report")
        dialog.instructions_edit.setPlainText("Produce the weekly HBM report")
        with self.assertRaises(ValueError):
            dialog.input_schema_edit.setPlainText("{not json")
            dialog.payload()
        dialog.input_schema_edit.setPlainText('{"type":"object"}')
        payload = dialog.payload()
        self.assertEqual(payload["name"], "Weekly Report")
        self.assertEqual(payload["result_format"], "json")
        self.assertEqual(payload["input_schema"], '{"type":"object"}')
        dialog.show_error("previous server-side error")
        self.assertEqual(dialog.error_label.text(), "previous server-side error")

    def test_task_editor_rejects_blank_name_and_instructions(self):
        dialog = TaskEditorDialog()
        with self.assertRaisesRegex(ValueError, "name is required"):
            dialog.payload()
        dialog.name_edit.setText("X")
        with self.assertRaisesRegex(ValueError, "[Ii]nstructions"):
            dialog.payload()

    def test_task_run_dialog_builds_overrides_only_for_changed_fields(self):
        dialog = TaskRunDialog(task={"task_id": "alpha", "name": "Weekly"}, available_workers=["codex", "claude"])
        self.assertEqual(dialog.overrides(), {})
        dialog.worker_combo.setCurrentText("codex")
        overrides = dialog.overrides()
        self.assertIn("worker", overrides)
        self.assertNotIn("profile", overrides)

    def test_save_run_as_task_dialog_requires_name(self):
        dialog = SaveRunAsTaskDialog(run_id="job-deadbeef")
        sent = []
        dialog.accepted_payload.connect(lambda payload: sent.append(payload))
        dialog._on_save()
        self.assertEqual(sent, [])
        self.assertIn("required", dialog.error_label.text().casefold())
        dialog.name_edit.setText("Weekly HBM report")
        dialog._on_save()
        self.assertEqual(sent, [{"name": "Weekly HBM report", "description": None}])

    def test_tasks_view_dispatches_signals_for_create_edit_and_run(self):
        view = TasksView()
        view.set_tasks(
            [
                {"task_id": "alpha", "name": "Weekly HBM", "version": 2, "default_worker": "codex"},
            ]
        )
        view.set_available_workers(["codex", "claude"])
        created_payloads = []
        view.task_create_submitted.connect(lambda payload: created_payloads.append(payload))
        view.show_create_editor()
        view.editor.name_edit.setText("Weekly HBM")
        view.editor.instructions_edit.setPlainText("Run the HBM analysis")
        view.editor._on_save()
        self.assertEqual(len(created_payloads), 1)
        self.assertEqual(created_payloads[0]["name"], "Weekly HBM")

        updated_payloads = []
        view.task_edit_submitted.connect(lambda tid, payload: updated_payloads.append((tid, payload)))
        view.show_edit_editor("alpha")
        view.editor.instructions_edit.setPlainText("Update the HBM report")
        view.editor._on_save()
        self.assertEqual(updated_payloads[0][0], "alpha")
        self.assertIn("Update", updated_payloads[0][1]["instructions"])

        runs = []
        view.task_run_submitted.connect(lambda tid, overrides: runs.append((tid, overrides)))
        view.show_run_dialog("alpha")
        view.runner.worker_combo.setCurrentText("codex")
        view.runner.notes_edit.setText("manual")
        view.runner.accepted.emit()
        self.assertEqual(runs[0][0], "alpha")
        self.assertEqual(runs[0][1]["worker"], "codex")
        self.assertEqual(runs[0][1]["notes"], "manual")


class MainWindowTasksRoutingTests(unittest.TestCase):
    """Exercise the tasks-related wiring on MainWindow without a real daemon."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        import tempfile

        from relay.config import Config
        from relay.gui.main_window import MainWindow

        tmp = tempfile.TemporaryDirectory()
        import pathlib as _pl

        home = _pl.Path(tmp.name) / "home"
        config = Config(home)
        config.init()
        from relay.compatibility import relay_home_id

        window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
        window.current_mode = "normal"
        self.requests = []
        window._request = lambda kind, path: self.requests.append([kind, str(path)])
        return window, tmp

    def test_show_tasks_switches_detail_view_and_refreshes(self):
        window, tmp = self._build()
        try:
            window._show_tasks()
            self.assertEqual(window.detail_view_mode, "tasks")
            self.assertEqual(self.requests, [["tasks", "/v1/tasks"]])
        finally:
            window.close()
            tmp.cleanup()

    def test_select_task_dispatches_detail_and_runs_calls(self):
        window, tmp = self._build()
        try:
            requests = []
            window._request = lambda kind, path: requests.append((kind, str(path)))
            window._select_task("task-1")
            self.assertEqual(
                requests,
                [
                    (("task_detail", "task-1"), "/v1/tasks/task-1"),
                    (("task_runs", "task-1"), "/v1/tasks/task-1/runs?limit=20"),
                ],
            )
        finally:
            window.close()
            tmp.cleanup()

    def test_task_responses_set_widget_state(self):
        window, tmp = self._build()
        try:
            window._show_tasks()
            window._refresh_tasks()
            window.pending[101] = "tasks"
            window._handle_response(
                101,
                {"tasks": [{"task_id": "task-1", "name": "Weekly HBM", "version": 1, "default_worker": "codex"}]},
                None,
            )
            self.assertIn("task-1", window.tasks_index)
            self.assertEqual(window.tasks_view.list.list_widget.count(), 1)
            window.selected_task_id = "task-1"
            window.pending[102] = ("task_detail", "task-1")
            window._handle_response(
                102,
                {"task": {"task_id": "task-1", "name": "Weekly HBM", "version": 2, "default_worker": "codex"}},
                None,
            )
            self.assertEqual(window.tasks_index["task-1"]["version"], 2)
            window.pending[103] = ("task_runs", "task-1")
            window._handle_response(
                103,
                {
                    "runs": [
                        {
                            "job_id": "job-1",
                            "status": "COMPLETED",
                            "completed_at": "2026-08-04",
                            "actual_worker": "codex",
                        }
                    ]
                },
                None,
            )
            self.assertIn("job-1", window.tasks_view.detail.run_browser.toPlainText().casefold())
        finally:
            window.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
