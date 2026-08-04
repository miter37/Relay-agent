"""Phase 4 Projects GUI widget tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.projects import (
    ProjectDetailView,
    ProjectEditorDialog,
    ProjectRunMonitorDialog,
    ProjectsListView,
    ProjectsView,
)


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

    def test_project_editor_payload_round_trip(self):
        dialog = ProjectEditorDialog(
            available_tasks=[{"name": "TA", "task_id": "ta"}],
            delivery_roots=[],
        )
        dialog.name_edit.setText("HBM report")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 0, 0, "collect")
        dialog._set_cell(dialog.nodes_table, 0, 1, "TA (ta)")
        dialog._on_add_node()
        dialog._set_cell(dialog.nodes_table, 1, 0, "analyze")
        dialog._set_cell(dialog.nodes_table, 1, 1, "TA (ta)")
        dialog._on_add_connection()
        dialog._set_cell(dialog.connections_table, 0, 0, "collect")
        dialog._set_cell(dialog.connections_table, 0, 1, "raw")
        dialog._set_cell(dialog.connections_table, 0, 2, "analyze")
        dialog._set_cell(dialog.connections_table, 0, 3, "A1")
        dialog._on_add_output()
        dialog._set_cell(dialog.outputs_table, 0, 0, "analyze")
        dialog._set_cell(dialog.outputs_table, 0, 1, "final")
        payload = dialog.payload()
        self.assertEqual(payload["name"], "HBM report")
        self.assertEqual([n["node_id"] for n in payload["nodes"]], ["collect", "analyze"])
        self.assertEqual(payload["connections"][0]["from_node"], "collect")
        self.assertEqual(payload["output_selection"][0]["role"], "final")

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
        dialog._set_cell(dialog.nodes_table, 0, 1, "TA (ta)")
        # checkpoint JSON with delivery target outside any allow-listed root
        dialog._set_cell(
            dialog.nodes_table,
            0,
            2,
            '{"enabled": true, "deliver_to": [{"kind": "folder", "path": "/no/such/path"}]}',
        )
        with self.assertRaisesRegex(ValueError, "not in allow-list"):
            dialog.payload()

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
        view.editor._set_cell(view.editor.nodes_table, 0, 1, "TA (ta)")
        view.editor._on_save()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["name"], "Weekly HBM")
        edited = []
        view.project_edit_submitted.connect(lambda pid, p: edited.append((pid, p)))
        view.show_edit_editor("p-1")
        view.editor.name_edit.setText("Weekly HBM")
        view.editor._on_add_node()
        view.editor._set_cell(view.editor.nodes_table, 0, 0, "collect")
        view.editor._set_cell(view.editor.nodes_table, 0, 1, "TA (ta)")
        view.editor.description_edit.setText("updated description")
        view.editor._on_save()
        self.assertEqual(edited[0][0], "p-1")
        self.assertEqual(edited[0][1]["description"], "updated description")
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
            self.assertEqual(window.detail_view_mode, "projects")
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


if __name__ == "__main__":
    unittest.main()
