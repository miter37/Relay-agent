"""Phase 3 Tasks GUI widget tests.

These tests instantiate widgets under ``QT_QPA_PLATFORM=offscreen`` so
they exercise the production rendering and signal wiring without needing
a desktop session.
"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.tasks import (
    InputDefinitionDialog,
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
        self.assertEqual(view.create_button.accessibleName(), "Register a new Task")
        self.assertFalse(view.empty_label.isHidden())
        view.set_tasks(
            [
                {"task_id": "alpha", "name": "Weekly HBM", "version": 2, "default_worker": "codex"},
                {"task_id": "beta", "name": "Daily Report", "version": 1, "default_worker": "auto"},
            ]
        )
        self.assertEqual(view.list_widget.count(), 2)
        self.assertTrue(view.empty_label.isHidden())
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
        view.search_edit.setText("missing")
        self.assertFalse(view.empty_label.isHidden())
        self.assertIn("match", view.empty_label.text())

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

    def test_task_editor_builds_input_schema_from_user_definitions(self):
        dialog = TaskEditorDialog()
        self.assertEqual(dialog.windowTitle(), "Register Task")
        dialog.name_edit.setText("Weekly Report")
        dialog.instructions_edit.setPlainText("Produce the weekly HBM report")
        dialog.input_definitions.definitions = [
            {
                "name": "City",
                "description": "Target city",
                "value_type": "text",
                "cardinality": "single",
                "required": True,
                "choices": [],
                "has_default": False,
                "default": None,
            },
            {
                "name": "Symbols",
                "description": "Symbols to compare",
                "value_type": "choice",
                "cardinality": "list",
                "required": False,
                "choices": ["A", "B"],
                "has_default": True,
                "default": ["A"],
            },
        ]
        payload = dialog.payload()
        schema = json.loads(payload["input_schema"])
        self.assertEqual(schema["required"], ["City"])
        self.assertEqual(schema["properties"]["Symbols"]["items"]["enum"], ["A", "B"])
        self.assertEqual(schema["properties"]["Symbols"]["default"], ["A"])
        self.assertEqual(payload["name"], "Weekly Report")
        self.assertEqual(payload["result_format"], "json")
        dialog.show_error("previous server-side error")
        self.assertEqual(dialog.error_label.text(), "previous server-side error")

    def test_task_editor_rejects_blank_name_and_instructions(self):
        dialog = TaskEditorDialog()
        with self.assertRaisesRegex(ValueError, "name is required"):
            dialog.payload()
        dialog.name_edit.setText("X")
        with self.assertRaisesRegex(ValueError, "[Ii]nstructions"):
            dialog.payload()

    def test_task_run_dialog_builds_schema_validated_inputs_and_overrides(self):
        dialog = TaskRunDialog(
            task={
                "task_id": "alpha",
                "name": "Weekly",
                "input_schema": json.dumps(
                    {
                        "type": "object",
                        "required": ["city", "period"],
                        "properties": {"city": {"type": "string"}, "period": {"type": "string"}},
                        "additionalProperties": False,
                    }
                ),
            },
            available_workers=["codex", "claude"],
        )
        with self.assertRaisesRegex(ValueError, "required"):
            dialog.overrides()
        dialog._input_fields["city"][0].setText("Seoul")
        dialog._input_fields["period"][0].setText("2026-08-06..2026-08-10")
        self.assertEqual(dialog.overrides()["inputs"], {"city": "Seoul", "period": "2026-08-06..2026-08-10"})
        dialog.worker_combo.setCurrentText("codex")
        overrides = dialog.overrides()
        self.assertIn("worker", overrides)
        self.assertNotIn("profile", overrides)
        self.assertEqual(overrides["inputs"], {"city": "Seoul", "period": "2026-08-06..2026-08-10"})

    def test_input_editor_and_run_form_preserve_number_and_boolean_lists(self):
        editor = InputDefinitionDialog()
        editor.name_edit.setText("Thresholds")
        editor.type_combo.setCurrentText("Number")
        editor.shape_combo.setCurrentText("List")
        editor.has_default.setChecked(True)
        editor.default.setPlainText("1\n2.5")
        self.assertEqual(editor.value()["default"], [1.0, 2.5])

        dialog = TaskRunDialog(
            task={
                "task_id": "alpha",
                "name": "Analyze",
                "input_schema": json.dumps(
                    {
                        "type": "object",
                        "properties": {
                            "thresholds": {"type": "array", "items": {"type": "number"}},
                            "flags": {"type": "array", "items": {"type": "boolean"}},
                        },
                        "additionalProperties": False,
                    }
                ),
            }
        )
        dialog._input_fields["thresholds"][0].setPlainText("1\n2.5")
        dialog._input_fields["flags"][0].setPlainText("true\nfalse")
        self.assertEqual(dialog.overrides()["inputs"], {"thresholds": [1.0, 2.5], "flags": [True, False]})

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
        view.runner.accepted.emit()
        self.assertEqual(runs[0][0], "alpha")
        self.assertEqual(runs[0][1]["worker"], "codex")


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
            self.assertEqual(window.active_section, "tasks")
            self.assertEqual(self.requests, [["tasks", "/v1/tasks"], ["profiles", "/v1/profiles"]])
        finally:
            window.close()
            tmp.cleanup()

    def test_global_register_task_action_only_creates_definition(self):
        window, tmp = self._build()
        try:
            posts = []
            window._request_post = lambda kind, path, payload: posts.append((kind, path, payload))

            window._show_task_registration()

            self.assertIs(window.detail_stack.currentWidget(), window.tasks_view)
            self.assertIsNotNone(window.tasks_view.editor)
            window.tasks_view.editor.name_edit.setText("Reusable weekly report")
            window.tasks_view.editor.instructions_edit.setPlainText("Prepare the report from supplied inputs.")
            window.tasks_view.editor._on_save()

            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0][0:2], ("task_create", "/v1/tasks"))
            self.assertNotEqual(posts[0][1], "/v1/jobs")
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

    def test_submitted_task_run_is_visible_and_restored_from_runs(self):
        window, tmp = self._build()
        try:
            window.pending[201] = ("task_run", "task-1")
            window._handle_response(
                201,
                {
                    "run": {
                        "job_id": "run-1",
                        "task_run_id": "run-1",
                        "title": "Seoul weather",
                        "status": "QUEUED",
                    }
                },
                None,
            )

            self.assertEqual(window.selected_job_id, "run-1")
            self.assertIn("run-1", window.jobs)
            self.assertTrue(window.runs_button.isChecked())
            self.assertIs(window.detail_stack.currentWidget(), window.runs_view)
            self.assertGreater(window.runs_view.run_list.topLevelItemCount(), 0)

            window._show_tasks()
            self.assertEqual(window.selected_job_id, "run-1")
            window._show_runs()

            self.assertEqual(window.selected_job_id, "run-1")
            self.assertTrue(window.runs_button.isChecked())
            self.assertIs(window.detail_stack.currentWidget(), window.runs_view)
            self.assertIn((("detail", "run-1"), "/v1/jobs/run-1"), map(tuple, self.requests))
        finally:
            window.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
