"""Phase 5 Routines GUI widget and routing tests."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.routines import RoutineDetailView, RoutineEditorDialog, RoutinesListView, RoutinesView


class RoutinesWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_list_filters_and_emits_selection(self):
        view = RoutinesListView()
        view.set_routines(
            [
                {"routine_id": "r-1", "name": "Daily report", "target_type": "task", "target_id": "t-1", "enabled": 1},
                {
                    "routine_id": "r-2",
                    "name": "Weekly project",
                    "target_type": "project",
                    "target_id": "p-1",
                    "enabled": 0,
                },
            ]
        )
        self.assertEqual(view.list_widget.count(), 2)
        view.search_edit.setText("report")
        self.assertEqual(view.list_widget.count(), 1)
        seen = []
        view.select_routine_requested.connect(seen.append)
        view._item_activated(view.list_widget.item(0))
        self.assertEqual(seen, ["r-1"])

    def test_detail_renders_runs_and_clears(self):
        view = RoutineDetailView()
        view.set_routine(
            {
                "routine_id": "r-1",
                "name": "Daily report",
                "target_type": "task",
                "target_id": "t-1",
                "enabled": 1,
                "timezone": "UTC",
                "next_run_at_utc": "2026-08-04T09:00:00+00:00",
            },
            [{"run_id": "rr-1", "status": "completed", "trigger_type": "routine"}],
        )
        self.assertEqual(view.title_label.text(), "Daily report")
        self.assertTrue(view.run_button.isEnabled())
        self.assertIn("rr-1", view.runs_browser.toPlainText())
        view.set_receipt({"routine": {"routine_id": "r-1"}, "runs": []})
        self.assertIn("routine_id", view.receipt_browser.toPlainText())
        child = []
        view.child_run_requested.connect(lambda kind, run_id: child.append((kind, run_id)))
        view._on_run_link(QUrl("relay://task/job-1"))
        self.assertEqual(child, [("task", "job-1")])
        view.clear()
        self.assertEqual(view.title_label.text(), "Routine")
        self.assertFalse(view.run_button.isEnabled())

    def test_editor_payload_and_validation(self):
        dialog = RoutineEditorDialog(
            available_tasks=[{"task_id": "t-1", "name": "Daily task"}],
            available_projects=[{"project_id": "p-1", "name": "Weekly project"}],
        )
        dialog.name_edit.setText("Daily report")
        dialog.rule_edit.setPlainText('{"type": "daily", "times": ["09:00"]}')
        payload = dialog.payload()
        self.assertEqual(payload["target_type"], "task")
        self.assertEqual(payload["target_id"], "t-1")
        self.assertEqual(payload["rule"]["timezone"], "UTC")
        # Every policy the core accepts is now implemented, so the editor offers all of them.
        from relay.routines.models import _VALID_OVERLAP

        offered = [dialog.overlap_combo.itemText(i) for i in range(dialog.overlap_combo.count())]
        self.assertEqual(sorted(offered), sorted(_VALID_OVERLAP))
        dialog.rule_edit.setPlainText("not json")
        with self.assertRaisesRegex(ValueError, "Rule must be valid JSON"):
            dialog.payload()

    def test_editor_preview_emits_rule_without_target_requirement(self):
        dialog = RoutineEditorDialog()
        dialog.rule_edit.setPlainText('{"type": "daily", "times": ["09:00"]}')
        seen = []
        dialog.preview_requested.connect(seen.append)
        dialog.preview_button.click()
        self.assertEqual(seen[0]["rule"]["timezone"], "UTC")
        dialog.set_preview([{"local_time": "2026-08-04T09:00", "instant_utc": "2026-08-04T00:00:00+00:00"}])
        self.assertIn("2026-08-04", dialog.preview_browser.toPlainText())

    def test_edit_editor_selects_existing_target(self):
        dialog = RoutineEditorDialog(
            routine={
                "routine_id": "r-1",
                "name": "Daily report",
                "target_type": "task",
                "target_id": "t-1",
                "rule_json": '{"type": "daily", "times": ["09:00"]}',
            },
            available_tasks=[{"task_id": "t-1", "name": "Daily task"}],
        )
        self.assertEqual(dialog.target_id_combo.currentData(), None)
        self.assertIn("t-1", dialog.target_id_combo.currentText())

    def test_view_emits_create_and_edit_payloads(self):
        view = RoutinesView()
        view.set_tasks([{"task_id": "t-1", "name": "Daily task"}])
        view.set_routines(
            [
                {
                    "routine_id": "r-1",
                    "name": "Old",
                    "target_type": "task",
                    "target_id": "t-1",
                    "rule": {"type": "daily", "times": ["09:00"]},
                }
            ]
        )
        created = []
        view.routine_create_submitted.connect(created.append)
        view.show_create_editor()
        view.editor.name_edit.setText("New")
        view.editor.rule_edit.setPlainText('{"type": "daily", "times": ["09:00"]}')
        view.editor._on_save()
        self.assertEqual(created[0]["name"], "New")
        edited = []
        view.routine_edit_submitted.connect(lambda rid, payload: edited.append((rid, payload)))
        view.show_edit_editor("r-1")
        view.editor._on_save()
        self.assertEqual(edited[0][0], "r-1")


class RoutinesMainWindowRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        from pathlib import Path

        from relay.compatibility import relay_home_id
        from relay.config import Config
        from relay.gui.main_window import MainWindow

        temp = tempfile.TemporaryDirectory()
        config = Config(Path(temp.name) / "home")
        config.init()
        window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
        window.current_mode = "normal"
        requests = []
        window._request = lambda kind, path: requests.append([kind, str(path)])
        return window, temp, requests

    def test_show_and_select_routines_dispatch_requests(self):
        window, temp, requests = self._build()
        try:
            window._show_routines()
            self.assertEqual(window.active_section, "routines")
            self.assertEqual(
                requests,
                [
                    ["routines", "/v1/routines?limit=200"],
                    ["routine_tasks", "/v1/tasks?limit=200"],
                    ["routine_projects", "/v1/projects?limit=200"],
                ],
            )
            requests.clear()
            window._select_routine("r-1")
            self.assertEqual(
                requests,
                [
                    [("routine_detail", "r-1"), "/v1/routines/r-1"],
                    [("routine_runs", "r-1"), "/v1/routines/r-1/runs?limit=100"],
                    [("routine_receipt", "r-1"), "/v1/routines/r-1/receipt"],
                ],
            )
        finally:
            window.close()
            temp.cleanup()

    def test_routine_responses_populate_detail(self):
        window, temp, _requests = self._build()
        try:
            window._show_routines()
            window.pending[1] = "routines"
            window._handle_response(1, {"routines": [{"routine_id": "r-1", "name": "Daily"}]}, None)
            self.assertIn("r-1", window.routines_index)
            self.assertEqual(window.routines_view.list.list_widget.count(), 1)
            window.selected_routine_id = "r-1"
            window.pending[2] = ("routine_detail", "r-1")
            window._handle_response(
                2,
                {"routine": {"routine_id": "r-1", "name": "Updated", "target_type": "task", "target_id": "t-1"}},
                None,
            )
            self.assertEqual(window.routines_view.detail.title_label.text(), "Updated")
        finally:
            window.close()
            temp.cleanup()

    def test_routine_mutations_use_expected_routes(self):
        window, temp, _requests = self._build()
        try:
            calls = []
            window._request_post = lambda kind, path, payload: calls.append((kind, path, payload))
            window._submit_create_routine({"name": "Daily"})
            window._submit_update_routine("r-1", {"name": "Updated"})
            window._submit_run_routine("r-1")
            self.assertEqual(calls[0], ("routine_create", "/v1/routines", {"name": "Daily"}))
            self.assertEqual(calls[1], (("routine_update", "r-1"), "/v1/routines/r-1", {"name": "Updated"}))
            self.assertEqual(calls[2], (("routine_run", "r-1"), "/v1/routines/r-1/run-now", {}))
        finally:
            window.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
