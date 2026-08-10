"""Phase 4 Projects GUI widget tests."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.projects import (
    ProjectDetailView,
    ProjectEditorDialog,
    ProjectRunMonitorDialog,
    ProjectsListView,
    ProjectsView,
)


def _select_task(dialog, row, task_id):
    combo = dialog.nodes_table.cellWidget(row, 1)
    combo.setCurrentIndex(combo.findData(task_id))


def _select_node(table, row, column, node_id):
    table.cellWidget(row, column).setCurrentText(node_id)


class ProjectsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_project_list_renders_filters_and_emits_select(self):
        view = ProjectsListView()
        view.set_projects(
            [
                {"project_id": "p-1", "name": "Weekly HBM report", "version": 2},
                {"project_id": "p-2", "name": "Daily cleaning", "version": 1},
            ]
        )
        self.assertEqual(view.list_widget.count(), 2)
        view.search_edit.setText("report")
        self.assertEqual(view.list_widget.count(), 1)
        view.search_edit.setText("")
        self.assertEqual(view.list_widget.count(), 2)
        seen = []
        view.select_project_requested.connect(lambda pid: seen.append(pid))
        view.list_widget.setCurrentRow(0)
        view._item_activated(view.list_widget.currentItem())
        view.list_widget.setCurrentRow(1)
        view._item_activated(view.list_widget.currentItem())
        self.assertIn("p-1", seen)
        self.assertIn("p-2", seen)

    def test_project_detail_renders_and_toggles_state(self):
        view = ProjectDetailView()
        view.set_project(
            {
                "project_id": "p-1",
                "name": "Weekly HBM report",
                "version": 2,
                "definition_json": (
                    '{"nodes": [{"node_id": "collect", "task_id": "t-1"}],"connections": [], "output_selection": []}'
                ),
            },
            [{"project_run_id": "pr-1", "status": "completed", "created_at": "2026-08-04"}],
        )
        self.assertEqual(view.title_label.text(), "Weekly HBM report")
        self.assertIn("v2", view.status_label.text())
        self.assertTrue(view.edit_button.isEnabled())
        self.assertTrue(view.delete_button.isEnabled())
        self.assertIn("pr-1", view.runs_browser.toPlainText().casefold())
        view.clear()
        self.assertEqual(view.title_label.text(), "Project")
        self.assertFalse(view.edit_button.isEnabled())

    def test_project_list_activation_emits_selection_once(self):
        view = ProjectsListView()
        view.set_projects([{"project_id": "p-1", "name": "One"}])
        seen = []
        view.select_project_requested.connect(seen.append)

        view._item_activated(view.list_widget.item(0))

        self.assertEqual(seen, ["p-1"])

    def test_project_editor_payload_round_trip(self):
        dialog = ProjectEditorDialog(
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )
        dialog.name_edit.setText("HBM report")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "collect")
        _select_task(dialog, 0, "ta")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 1, 0, "analyze")
        _select_task(dialog, 1, "ta")
        dialog._on_add_connection()
        _select_node(dialog.connections_table, 0, 0, "collect")
        dialog._set_cell(dialog.connections_table, 0, 1, "raw")
        _select_node(dialog.connections_table, 0, 2, "analyze")
        dialog._set_cell(dialog.connections_table, 0, 3, "A1")
        dialog._on_add_output()
        _select_node(dialog.outputs_table, 0, 0, "analyze")
        dialog._set_cell(dialog.outputs_table, 0, 1, "final")
        payload = dialog.payload()
        self.assertEqual(payload["name"], "HBM report")
        self.assertEqual([n["node_id"] for n in payload["nodes"]], ["collect", "analyze"])
        self.assertEqual([n["task_id"] for n in payload["nodes"]], ["ta", "ta"])
        self.assertEqual(payload["connections"][0]["from_node"], "collect")
        self.assertEqual(payload["output_selection"][0]["role"], "final")

    def test_project_editor_omits_orchestrator_when_never_enabled(self):
        dialog = ProjectEditorDialog(available_tasks=[{"name": "TA", "task_id": "ta"}], delivery_roots=[])
        dialog.name_edit.setText("Solo")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "a")
        _select_task(dialog, 0, "ta")

        payload = dialog.payload()

        self.assertNotIn("orchestrator", payload)

    def test_project_editor_orchestrator_round_trip(self):
        dialog = ProjectEditorDialog(available_tasks=[{"name": "TA", "task_id": "ta"}], delivery_roots=[])
        dialog.name_edit.setText("Solo")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "a")
        _select_task(dialog, 0, "ta")
        dialog.orchestrator_enabled_checkbox.setChecked(True)
        dialog.orchestrator_worker_edit.setText("claude")
        dialog.orchestrator_model_edit.setText("claude-opus-4-6")
        dialog.orchestrator_profile_edit.setText("default")
        dialog.orchestrator_max_repairs_node_spin.setValue(3)
        dialog.orchestrator_max_repairs_run_spin.setValue(9)
        dialog.orchestrator_max_llm_calls_spin.setValue(12)

        payload = dialog.payload()

        self.assertEqual(
            payload["orchestrator"],
            {
                "enabled": True,
                "worker": "claude",
                "model": "claude-opus-4-6",
                "profile": "default",
                "max_repair_attempts_per_node": 3,
                "max_repair_attempts_per_run": 9,
                "max_llm_calls_per_run": 12,
            },
        )

    def test_project_editor_populates_orchestrator_from_existing_project(self):
        dialog = ProjectEditorDialog(
            project={
                "name": "Existing",
                "description": "",
                "definition_json": json.dumps(
                    {
                        "name": "Existing",
                        "nodes": [{"node_id": "a", "task_id": "ta"}],
                        "connections": [],
                        "output_selection": [],
                        "orchestrator": {
                            "enabled": True,
                            "worker": "codex",
                            "model": "gpt-5.6-luna",
                            "max_llm_calls_per_run": 5,
                        },
                    }
                ),
            },
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )

        self.assertTrue(dialog.orchestrator_enabled_checkbox.isChecked())
        self.assertEqual(dialog.orchestrator_worker_edit.text(), "codex")
        self.assertEqual(dialog.orchestrator_model_edit.text(), "gpt-5.6-luna")
        self.assertEqual(dialog.orchestrator_max_llm_calls_spin.value(), 5)

    def test_project_editor_unchecking_orchestrator_preserves_settings_for_reenable(self):
        """Disabling must not throw away worker/budget settings a re-enable would want back."""
        dialog = ProjectEditorDialog(
            project={
                "name": "Existing",
                "description": "",
                "definition_json": json.dumps(
                    {
                        "name": "Existing",
                        "nodes": [{"node_id": "a", "task_id": "ta"}],
                        "connections": [],
                        "output_selection": [],
                        "orchestrator": {"enabled": True, "worker": "codex", "max_llm_calls_per_run": 5},
                    }
                ),
            },
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )

        dialog.orchestrator_enabled_checkbox.setChecked(False)
        payload = dialog.payload()

        self.assertEqual(payload["orchestrator"]["enabled"], False)
        self.assertEqual(payload["orchestrator"]["worker"], "codex")
        self.assertEqual(payload["orchestrator"]["max_llm_calls_per_run"], 5)

    def test_project_editor_task_column_is_a_picker_not_free_text(self):
        # The defect this fixes: a user had to hand-type "Name (task_id)" into a
        # plain text cell to pick a Task. It is now a combo box keyed by task_id.
        dialog = ProjectEditorDialog(
            available_tasks=[{"name": "Research", "task_id": "t-research"}],
            delivery_roots=[],
        )
        dialog._on_add_node()
        combo = dialog.nodes_table.cellWidget(0, 1)
        self.assertIsInstance(combo, QComboBox)
        self.assertFalse(combo.isEditable())
        # A single registered Task is pre-selected; nothing to type or match.
        self.assertEqual(combo.currentData(), "t-research")

    def test_project_editor_preserves_task_id_missing_from_the_registry_on_edit(self):
        # Editing an existing Project whose Task was since deleted must not
        # silently swap in some other Task the next time the row is saved.
        dialog = ProjectEditorDialog(
            project={
                "project_id": "p-1",
                "name": "Old",
                "definition_json": (
                    '{"nodes": [{"node_id": "collect", "task_id": "t-gone"}],"connections": [], "output_selection": []}'
                ),
            },
            available_tasks=[{"name": "Other", "task_id": "t-other"}],
            delivery_roots=[],
        )
        combo = dialog.nodes_table.cellWidget(0, 1)
        self.assertEqual(combo.currentData(), "t-gone")
        payload = dialog.payload()
        self.assertEqual(payload["nodes"][0]["task_id"], "t-gone")

    def test_project_editor_connection_pickers_offer_typed_node_ids(self):
        # Connections/outputs reference node_ids already typed into the Nodes
        # table, via a picker, instead of a second freehand field that could
        # typo a reference to a node that does not exist.
        dialog = ProjectEditorDialog(
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "collect")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 1, 0, "analyze")
        dialog._on_add_connection()
        from_combo = dialog.connections_table.cellWidget(0, 0)
        self.assertIsInstance(from_combo, QComboBox)
        self.assertTrue(from_combo.isEditable())  # still escapable, not a hard lock-in
        offered = {from_combo.itemText(i) for i in range(from_combo.count())}
        self.assertEqual(offered, {"collect", "analyze"})

    def test_project_editor_rejects_empty_name_and_nodes(self):
        dialog = ProjectEditorDialog(available_tasks=[])
        with self.assertRaisesRegex(ValueError, "name is required"):
            dialog.payload()
        dialog.name_edit.setText("X")
        with self.assertRaisesRegex(ValueError, "at least one node"):
            dialog.payload()

    def test_project_editor_rejects_outside_delivery_root(self):
        dialog = ProjectEditorDialog(
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )
        dialog.name_edit.setText("X")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "collect")
        _select_task(dialog, 0, "ta")
        # checkpoint JSON with delivery target outside any allow-listed root
        dialog._set_cell(
            dialog.nodes_table,
            0,
            2,
            '{"enabled": true, "deliver_to": [{"kind": "folder", "path": "/no/such/path"}]}',
        )
        with self.assertRaisesRegex(ValueError, "not in allow-list"):
            dialog.payload()

    def test_project_editor_save_stays_open_until_close_after_save(self):
        # The defect this fixes: Save used to close the dialog before the POST
        # even went out, so any backend rejection lost every typed row.
        dialog = ProjectEditorDialog(
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )
        dialog.name_edit.setText("X")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "collect")
        emitted = []
        dialog.accepted_payload.connect(emitted.append)

        dialog._on_save()

        self.assertEqual(len(emitted), 1)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)  # not closed at all yet
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(dialog.nodes_table.rowCount(), 1)  # nothing was thrown away

        dialog.close_after_save()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_project_editor_report_save_error_keeps_every_typed_row(self):
        dialog = ProjectEditorDialog(
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )
        dialog.name_edit.setText("X")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "collect")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 1, 0, "analyze")
        dialog._on_save()

        dialog.report_save_error("Task not found: t-missing")

        self.assertEqual(dialog.error_label.text(), "Task not found: t-missing")
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertEqual(dialog.nodes_table.rowCount(), 2)
        self.assertEqual(dialog._row_text(dialog.nodes_table, 0, 0), "collect")
        self.assertEqual(dialog._row_text(dialog.nodes_table, 1, 0), "analyze")
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_project_run_monitor_dispatches_actions(self):
        dialog = ProjectRunMonitorDialog(
            project_run_id="pr-1",
            project_run={"status": "running", "trigger_type": "manual", "created_at": "2026-08-04"},
            steps=[{"node_id": "collect", "task_id": "ta", "status": "running"}],
            nodes=[{"node_id": "collect"}, {"node_id": "analyze"}],
        )
        seen = []
        dialog.accepted_action.connect(lambda action, payload: seen.append((action, payload)))
        dialog.refresh_button.click()
        self.assertIn(("refresh", {"project_run_id": "pr-1"}), seen)
        dialog.cancel_button.click()
        self.assertIn(("cancel", {"project_run_id": "pr-1"}), seen)
        dialog.reexec_node_edit.setText("collect")
        dialog.reexec_button.click()
        self.assertIn(
            (
                "partial-reexecute",
                {"project_run_id": "pr-1", "from_node": "collect", "cascade": True},
            ),
            seen,
        )
        # Empty reexecute node must NOT trigger; only the help label changes.
        dialog.reexec_node_edit.setText("")
        before = len(seen)
        dialog.reexec_button.click()
        self.assertEqual(len(seen), before)

    def test_projects_view_dispatches_signals_for_create_edit_and_run(self):
        view = ProjectsView()
        view.set_tasks([{"name": "TA", "task_id": "ta"}])
        view.set_projects([{"project_id": "p-1", "name": "Weekly HBM", "version": 2}])
        created = []
        view.project_create_submitted.connect(lambda p: created.append(p))
        view.show_create_editor()
        view.editor.name_edit.setText("Weekly HBM")
        view.editor._on_add_node()
        view.editor._set_cell(view.editor.nodes_table, 0, 0, "collect")
        view.editor._on_save()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["name"], "Weekly HBM")
        # Save no longer closes the dialog itself: the caller (MainWindow) does,
        # once the daemon confirms the write.
        self.assertIsNotNone(view.editor)
        view.editor.close_after_save()
        self.assertIsNone(view.editor)

        edited = []
        view.project_edit_submitted.connect(lambda pid, p: edited.append((pid, p)))
        view.show_edit_editor("p-1")
        view.editor.name_edit.setText("Weekly HBM")
        view.editor._on_add_node()
        view.editor._set_cell(view.editor.nodes_table, 0, 0, "collect")
        view.editor.description_edit.setText("updated description")
        view.editor._on_save()
        self.assertEqual(edited[0][0], "p-1")
        self.assertEqual(edited[0][1]["description"], "updated description")
        view.editor.close_after_save()
        run = []
        view.project_run_submitted.connect(lambda run_id, payload: run.append((run_id, payload)))
        view._on_run_action("partial-reexecute", {"project_run_id": "pr-1", "from_node": "collect", "cascade": False})
        self.assertEqual(run[0][0], "pr-1")
        self.assertEqual(run[0][1]["action"], "partial-reexecute")
        self.assertEqual(run[0][1]["from_node"], "collect")
        self.assertEqual(run[0][1]["cascade"], False)


class ProjectsMainWindowRoutingTests(unittest.TestCase):
    """Exercise Projects navigation, dispatch, and response handling."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        import tempfile

        from relay.config import Config
        from relay.gui.main_window import MainWindow

        tmp = tempfile.TemporaryDirectory()
        from pathlib import Path as _pl

        home = _pl(tmp.name) / "home"
        config = Config(home)
        config.init()
        from relay.compatibility import relay_home_id

        window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
        window.current_mode = "normal"
        self.requests = []
        window._request = lambda kind, path: self.requests.append([kind, str(path)])
        return window, tmp

    def test_show_projects_switches_view_and_refreshes(self):
        window, tmp = self._build()
        try:
            window._show_projects()
            self.assertEqual(window.active_section, "projects")
            self.assertEqual(self.requests[0][0], "projects")
            self.assertEqual(self.requests[0][1], "/v1/projects")
            self.assertEqual(self.requests[1][0], "project_tasks")
            self.assertEqual(self.requests[1][1], "/v1/tasks?limit=200")
        finally:
            window.close()
            tmp.cleanup()

    def test_select_project_dispatches_detail_and_runs(self):
        window, tmp = self._build()
        try:
            window._select_project("p-1")
            expected = [
                [("project_detail", "p-1"), "/v1/projects/p-1"],
                [("project_runs", "p-1"), "/v1/projects/p-1/runs"],
            ]
            self.assertEqual(self.requests, expected)
        finally:
            window.close()
            tmp.cleanup()

    def test_project_run_requires_confirmation_before_post(self):
        window, tmp = self._build()
        try:
            window.projects_index["p-1"] = {"project_id": "p-1", "name": "Weekly HBM"}
            requests = []
            window._request_post = lambda kind, path, payload: requests.append((kind, path, payload))

            with patch("relay.gui.main_window.QMessageBox.question", return_value=QMessageBox.Cancel):
                window._submit_create_project_run("p-1")
            self.assertEqual(requests, [])

            with patch("relay.gui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
                window._submit_create_project_run("p-1")
            self.assertEqual(requests, [(("project_run_create", "p-1"), "/v1/projects/p-1/run", {})])
        finally:
            window.close()
            tmp.cleanup()

    def test_projects_response_populates_widget(self):
        window, tmp = self._build()
        try:
            window._show_projects()
            window.pending[101] = "projects"
            window._handle_response(
                101,
                {"projects": [{"project_id": "p-1", "name": "Weekly HBM", "version": 2}]},
                None,
            )
            self.assertIn("p-1", window.projects_index)
            self.assertEqual(window.projects_view.list.list_widget.count(), 1)
            window.selected_project_id = "p-1"
            window.pending[102] = ("project_detail", "p-1")
            window._handle_response(
                102,
                {"project": {"project_id": "p-1", "name": "Weekly HBM", "version": 3}},
                None,
            )
            self.assertEqual(window.projects_index["p-1"]["version"], 3)
        finally:
            window.close()
            tmp.cleanup()

    def test_project_create_error_reports_into_the_still_open_editor(self):
        # The defect this fixes: a rejected create used to fall through to a
        # generic "try again" banner and the dialog (with the daemon's actual
        # error_code/error_message sitting unused in the response payload) was
        # already gone by the time the response arrived.
        window, tmp = self._build()
        try:
            window.projects_view.set_tasks([{"name": "TA", "task_id": "ta"}])
            window.projects_view.show_create_editor()
            editor = window.projects_view.editor
            editor.name_edit.setText("X")
            editor._on_add_node()
            editor._set_cell(editor.nodes_table, 0, 0, "collect")
            editor._on_save()
            self.assertFalse(editor.save_button.isEnabled())

            window.pending[201] = "project_create"
            window._handle_response(
                201,
                {"ok": False, "error_code": "PROJECT_TASK_MISSING", "error_message": "Task not found: ta"},
                "Bad Request",
            )

            self.assertIs(window.projects_view.editor, editor)  # dialog was not destroyed
            self.assertEqual(editor.error_label.text(), "Task not found: ta")
            self.assertTrue(editor.save_button.isEnabled())  # user can fix and retry
            self.assertEqual(editor._row_text(editor.nodes_table, 0, 0), "collect")  # nothing lost
        finally:
            window.close()
            tmp.cleanup()

    def test_project_create_success_closes_the_editor(self):
        window, tmp = self._build()
        try:
            window.projects_view.set_tasks([{"name": "TA", "task_id": "ta"}])
            window.projects_view.show_create_editor()
            editor = window.projects_view.editor
            editor.name_edit.setText("X")
            editor._on_add_node()
            editor._set_cell(editor.nodes_table, 0, 0, "collect")
            editor._on_save()

            window.pending[202] = "project_create"
            window._handle_response(
                202,
                {"ok": True, "project": {"project_id": "p-new", "name": "X"}},
                None,
            )

            self.assertIsNone(window.projects_view.editor)
            self.assertIn("p-new", window.projects_index)
        finally:
            window.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
