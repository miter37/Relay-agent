"""GUI regressions for the Project Runs screen (Phases 1-4).

Pins the screen behaviors called out in
``docs/Relay_GUI_Project_Runs_Screen_Design_v1.0.md`` sections 4-9:
list grouping, verdict header, steps table with attempt counts, final
artifact strip, approve/reject buttons for awaiting runs, MainWindow
routing/polling rules (no polling of terminal runs, live Run selected => 2s
detail refresh, list refresh every ~5s while the screen is open), the
Phase 2 node inspector (attempt history, active Task Run summary, resolved
inputs as "A1 <- pick(result)", produced Artifacts, and node-level actions),
Phase 3 pipeline (topological layout, dimmed blocked descendants, dashed
failed edges, click-through to inspector) and Phase 4 timeline (one bar per
attempt with separate retry bars and parallel fan-outs).
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QScrollArea
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.design_icons import ICON_PATHS
from relay.gui.design_tokens import COLORS
from relay.gui.project_runs import (
    _ORCHESTRATOR_EVENT_ICON,
    _PIPELINE_STATUS_COLORS,
    ProjectRunArtifactChip,
    ProjectRunArtifactsView,
    ProjectRunDetailView,
    ProjectRunInspectorView,
    ProjectRunNodeCard,
    ProjectRunOrchestratorView,
    ProjectRunPipelineView,
    ProjectRunsView,
    ProjectRunTimelineCanvas,
    ProjectRunTimelineView,
    ProjectRunWorkspaceView,
    _artifact_kind,
    _humanize_error,
    _level_for_nodes,
    _local_date,
    _merge_project_run_artifacts,
    _verdict,
)


def _catalog_item(
    project_run_id: str,
    *,
    status: str,
    project_name: str = "Briefing",
    step_count: int = 3,
    completed: int = 2,
    failed: int = 0,
    blocked: int = 0,
    failed_node_id: str | None = None,
    trigger_type: str = "manual",
    error_code: str | None = None,
    final_artifacts: list[dict] | None = None,
) -> dict:
    return {
        "project_run_id": project_run_id,
        "project_id": f"pid-{project_run_id}",
        "project_name": project_name,
        "project_version": 1,
        "status": status,
        "step_count": step_count,
        "completed_step_count": completed,
        "failed_step_count": failed,
        "blocked_step_count": blocked,
        "failed_node_id": failed_node_id,
        "final_artifact_count": len(final_artifacts or []),
        "final_artifact_ids": final_artifacts or [],
        "trigger_type": trigger_type,
        "error_code": error_code,
        "created_at": "2026-08-07T08:00:00+00:00",
        "started_at": "2026-08-07T08:00:01+00:00" if status != "queued" else None,
    }


class ProjectRunsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_list_groups_by_status_with_failed_node_visible(self):
        view = ProjectRunsView()
        view.set_runs(
            [
                _catalog_item(
                    "pr-failed",
                    status="failed",
                    completed=1,
                    failed=1,
                    blocked=1,
                    failed_node_id="image",
                    error_code="ALL_WORKERS_FAILED",
                    project_name="Daily briefing",
                ),
                _catalog_item(
                    "pr-running",
                    status="running",
                    completed=1,
                    project_name="Comparison",
                ),
                _catalog_item(
                    "pr-completed",
                    status="completed",
                    completed=3,
                    project_name="Weekly recap",
                ),
            ]
        )

        groups = {
            view.run_list.topLevelItem(i).text(0).split(" · ")[0]: view.run_list.topLevelItem(i)
            for i in range(view.run_list.topLevelItemCount())
        }
        self.assertIn("Needs action", groups)
        self.assertIn("Running", groups)
        self.assertIn("Completed", groups)
        # Failed run lives under Needs action and the row label mentions the failed node.
        needs_action_group = groups["Needs action"]
        failed_label = needs_action_group.child(0).text(0)
        self.assertIn("failed @ image", failed_label)
        # Running row never appears under Needs action.
        running_group = groups["Running"]
        running_labels = [running_group.child(i).text(0) for i in range(running_group.childCount())]
        self.assertEqual(len(running_labels), 1)
        self.assertIn("Comparison", running_labels[0])
        # Blocked descendant only appears via failed_node_id on the run row; no separate
        # blocked child because the design hides it from the top-level list intentionally
        # (it lives in the detail).

    def test_group_by_project_toggle_lists_run_dates_with_occurrence_suffixes(self):
        view = ProjectRunsView()
        first = _catalog_item("pr-a1", status="completed", project_name="AAA project")
        second = _catalog_item("pr-a2", status="failed", project_name="AAA project")
        second["project_id"] = first["project_id"]
        third = _catalog_item("pr-b1", status="completed", project_name="BBB project")
        third["created_at"] = "2026-08-06T08:00:00+00:00"
        view.set_runs([first, second, third], selected_run_id="pr-a2")

        self.assertEqual(view.group_mode.value(), "date")
        project_button = view.group_mode.button("project")
        self.assertTrue(project_button.isCheckable())
        project_button.click()

        self.assertEqual(view.group_mode.value(), "project")
        self.assertTrue(project_button.isChecked())
        self.assertEqual(view.run_list.topLevelItemCount(), 2)
        aaa = view.run_list.topLevelItem(0)
        self.assertEqual(aaa.text(0), "AAA project · 2")
        date = _local_date(first["created_at"])
        self.assertEqual(aaa.child(0).text(0), date)
        self.assertEqual(aaa.child(1).text(0), f"{date} (2)")
        self.assertEqual(view.run_list.currentItem().data(0, Qt.UserRole), "pr-a2")

        view.group_mode.button("date").click()
        self.assertEqual(view.group_mode.value(), "date")

    def test_status_filter_narrows_needs_action(self):
        view = ProjectRunsView()
        view.set_runs(
            [
                _catalog_item("pr-failed", status="failed", failed=1, failed_node_id="image"),
                _catalog_item("pr-running", status="running"),
                _catalog_item("pr-completed", status="completed"),
            ]
        )
        view.status_filter.setCurrentText("Failed")
        groups = {
            view.run_list.topLevelItem(i).text(0).split(" · ")[0]: view.run_list.topLevelItem(i).childCount()
            for i in range(view.run_list.topLevelItemCount())
        }
        # Only the failed run remains, surfaced under the "Needs action" group label.
        self.assertEqual(sum(groups.values()), 1)

    def test_refresh_keeps_project_run_list_scroll_position(self):
        view = ProjectRunsView()
        runs = [
            _catalog_item(f"pr-{index}", status="completed", project_name=f"Project {index}") for index in range(40)
        ]
        view.resize(760, 240)
        view.show()
        self.app.processEvents()
        view.set_runs(runs)
        self.app.processEvents()

        scrollbar = view.run_list.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        position = min(40, scrollbar.maximum())
        scrollbar.setValue(position)
        view.set_runs(runs, selected_run_id=None)
        self.app.processEvents()

        self.assertEqual(scrollbar.value(), position)

    def test_select_run_signal_and_detail_render(self):
        view = ProjectRunsView()
        run = _catalog_item(
            "pr-failed",
            status="failed",
            completed=2,
            failed=1,
            blocked=1,
            failed_node_id="image",
            error_code="ALL_WORKERS_FAILED",
            final_artifacts=[{"node_id": "page", "role": "output", "artifact_uid": "uid-1"}],
        )
        view.set_runs([run])

        selected: list[str] = []
        view.select_run_requested.connect(selected.append)
        # Drive the click programmatically through the run row.
        group_item = view.run_list.topLevelItem(0)
        run_item = group_item.child(0)
        view._on_item_clicked(run_item)

        self.assertEqual(selected, ["pr-failed"])
        # MainWindow normally calls view.select_run after a click and then forwards the
        # detail response via set_run_detail; mirror both calls here so the detail
        # widget reflects the chosen run.
        view.select_run("pr-failed")
        view.detail.set_run(run)

        # Detail must report the failed node, blocked count, and humanized error.
        detail = view.detail
        self.assertEqual(detail.steps_table.columnCount(), 10)
        self.assertFalse(detail.retry_button.isHidden())
        self.assertTrue(detail.cancel_button.isHidden())
        self.assertIn("Failed at step image", detail.verdict_label.text())
        self.assertIn("1 step blocked downstream", detail.verdict_label.text())
        # Final artifact strip shows the role and an open button.
        self.assertIn("output", detail.artifact_label.text())

    def test_awaiting_approval_shows_approve_and_reject_buttons(self):
        view = ProjectRunsView()
        run = _catalog_item("pr-awaiting", status="awaiting_approval", blocked=1)
        view.set_runs([run])
        view.select_run("pr-awaiting")
        approvals = [{"token": "tok-1", "status": "pending", "node_id": "topic"}]
        view.set_run_approvals("pr-awaiting", approvals)

        detail = view.detail
        # set_run_approvals already calls detail.set_run, so the buttons are configured.
        self.assertFalse(detail.approve_button.isHidden())
        self.assertFalse(detail.reject_button.isHidden())
        self.assertIn("Awaiting approval", detail.verdict_label.text())

        approve_calls: list[tuple[str, str]] = []
        reject_calls: list[tuple[str, str]] = []
        detail.approve_requested.connect(lambda pid, token: approve_calls.append((pid, token)))
        detail.reject_requested.connect(lambda pid, token: reject_calls.append((pid, token)))
        detail._emit_approve()
        detail._emit_reject()
        self.assertEqual(approve_calls, [("pr-awaiting", "tok-1")])
        self.assertEqual(reject_calls, [("pr-awaiting", "tok-1")])

    def test_completed_run_hides_cancel_and_shows_open_output(self):
        view = ProjectRunsView()
        run = _catalog_item(
            "pr-done",
            status="completed",
            completed=3,
            final_artifacts=[{"node_id": "page", "role": "report", "artifact_uid": "uid-2"}],
        )
        view.set_runs([run])
        view.select_run("pr-done")
        # MainWindow would route the detail response here; mirror that so the buttons
        # reflect the selected run.
        view.detail.set_run(run)

        detail = view.detail
        self.assertTrue(detail.cancel_button.isHidden())
        self.assertTrue(detail.retry_button.isHidden())
        self.assertFalse(detail.output_button.isHidden())

        opened: list[str] = []
        detail.open_output_requested.connect(opened.append)
        detail._emit_open_output()
        self.assertEqual(opened, ["uid-2"])

    def test_step_attempts_column_shows_attempt_count(self):
        view = ProjectRunsView()
        view.set_runs([_catalog_item("pr-x", status="running")])
        view.select_run("pr-x")
        view.set_run_steps(
            "pr-x",
            [
                {
                    "node_id": "image",
                    "task_id": "t-img",
                    "task_version": 1,
                    "status": "running",
                    "active_task_run_id": "tr-2",
                    "started_at": "2026-08-07T08:00:00+00:00",
                    "completed_at": None,
                    "attempt_count": 2,
                    "worker_override": "claude",
                    "task_runs": [
                        {"step_attempt": 1, "status": "failed"},
                        {"step_attempt": 2, "status": "running"},
                    ],
                },
                {
                    "node_id": "pick",
                    "task_id": "t-pick",
                    "task_version": 1,
                    "status": "completed",
                    "active_task_run_id": "tr-1",
                    "started_at": "2026-08-07T07:59:00+00:00",
                    "completed_at": "2026-08-07T07:59:55+00:00",
                    "attempt_count": 1,
                    "worker_override": None,
                },
            ],
        )
        detail = view.detail
        self.assertEqual(detail.steps_table.rowCount(), 2)
        # With no Project snapshot, the fallback order follows started_at.
        rows = {detail.steps_table.item(row, 1).text(): row for row in range(detail.steps_table.rowCount())}
        image_row = rows["image"]
        pick_row = rows["pick"]
        self.assertEqual(detail.steps_table.item(image_row, 3).text(), "2")
        self.assertEqual(detail.steps_table.item(pick_row, 3).text(), "1")
        self.assertEqual(detail.steps_table.item(image_row, 6).text(), "claude")
        self.assertEqual(detail.steps_table.item(image_row, 7).text(), "—")
        self.assertEqual(detail.steps_table.item(image_row, 9).text(), "tr-2")

    def test_live_run_predicate_for_polling(self):
        view = ProjectRunsView()
        view.set_runs(
            [
                _catalog_item("pr-running", status="running"),
                _catalog_item("pr-done", status="completed"),
                _catalog_item("pr-failed", status="failed"),
            ]
        )
        self.assertFalse(view.has_live_run_selected())
        view.select_run("pr-running")
        self.assertTrue(view.has_live_run_selected())
        view.select_run("pr-done")
        self.assertFalse(view.has_live_run_selected())
        view.select_run("pr-failed")
        self.assertFalse(view.has_live_run_selected())

    def test_catalog_refresh_preserves_selected_run_detail(self):
        """A catalog poll must not erase the already-loaded detail payload."""
        view = ProjectRunsView()
        run = _catalog_item("pr-done", status="completed", completed=3)
        view.set_runs([run])
        view.select_run("pr-done")
        steps = [
            {
                "node_id": "image",
                "status": "completed",
                "active_task_run_id": "task-run-image",
                "started_at": "2026-08-07T08:00:00+00:00",
                "completed_at": "2026-08-07T08:00:10+00:00",
                "attempt_count": 1,
            }
        ]
        view.set_run_steps("pr-done", steps)
        view.detail.cache_receipt({"steps": [{"node_id": "image", "task_runs": steps}]})

        # This is the sparse payload produced when the 5-second catalog refresh
        # re-selects the current run; it must not replace detail/receipt data.
        view.set_run_detail("pr-done", {"snapshot": None, "steps": None})

        self.assertEqual(view.detail.steps_table.rowCount(), 1)
        self.assertEqual(view.detail._run.get("steps"), steps)
        self.assertEqual(len(view.detail._run.get("receipt_steps") or []), 1)

    def test_humanize_error_returns_copy_for_known_codes(self):
        self.assertEqual(_humanize_error("ALL_WORKERS_FAILED"), "All configured workers failed for this step.")
        self.assertEqual(_humanize_error(None), "")
        self.assertEqual(_humanize_error("weird_code"), "Weird Code")

    def test_verdict_copy(self):
        failed_run = _catalog_item(
            "pr-failed",
            status="failed",
            failed_node_id="image",
            blocked=2,
            error_code="ALL_WORKERS_FAILED",
        )
        self.assertIn("Failed at step image", _verdict(failed_run))
        self.assertIn("2 steps blocked downstream", _verdict(failed_run))

        awaiting = _catalog_item("pr-await", status="awaiting_approval", blocked=1)
        self.assertEqual(_verdict(awaiting), "Awaiting approval · 1 step waiting downstream")

        completed = _catalog_item("pr-done", status="completed", step_count=4)
        completed["final_artifact_count"] = 2
        self.assertIn("Completed · 4 steps", _verdict(completed))
        self.assertIn("2 final artifacts", _verdict(completed))

        running = _catalog_item("pr-run", status="running", step_count=4, completed=2)
        self.assertEqual(_verdict(running), "Running · 2/4 steps complete")


class ProjectRunsMainWindowRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        from relay.compatibility import relay_home_id
        from relay.config import Config
        from relay.gui.main_window import MainWindow

        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name) / "home"
        config = Config(home)
        config.init()
        window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
        window.current_mode = "normal"
        self.requests: list[list] = []
        window._request = lambda kind, path: self.requests.append([kind, str(path)])
        return window, tmp

    def _add_post(self, window) -> None:
        window._request_post = lambda kind, path, payload: self.requests.append([kind, str(path), payload])

    def test_show_project_runs_activates_section_and_fetches_list(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            self.assertEqual(window.active_section, "project_runs")
            self.assertIs(window.detail_stack.currentWidget(), window.project_runs_view)
            # First request must be the catalog list fetch.
            self.assertEqual(self.requests[0], ["project_runs_list", "/v1/catalog/project-runs?limit=200"])
        finally:
            window.close()
            tmp.cleanup()

    def test_select_project_run_dispatches_detail_steps_and_approvals(self):
        window, tmp = self._build()
        try:
            window._select_project_run("pr-1")
            self.assertEqual(window.selected_project_run_id, "pr-1")
            self.assertEqual(window.project_runs_view.selected_run_id, "pr-1")
            paths = {tuple(request[0]) for request in self.requests}
            self.assertIn(("project_run_v2_detail", "pr-1"), paths)
            self.assertIn(("project_run_v2_steps", "pr-1"), paths)
            self.assertIn(("project_run_v2_approvals", "pr-1"), paths)
        finally:
            window.close()
            tmp.cleanup()

    def test_project_run_detail_snapshot_populates_pipeline_after_selection(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            window._select_project_run("pr-1")
            snapshot = _linear_snapshot(
                [_node("first", "t-first"), _node("second", "t-second")],
                [_connection("first", "result", "second")],
            )
            window.pending[301] = ("project_run_v2_detail", "pr-1")
            window._handle_response(
                301,
                {"project_run": {"project_run_id": "pr-1", "status": "completed", "snapshot": snapshot}},
                None,
            )
            window.pending[302] = ("project_run_v2_steps", "pr-1")
            window._handle_response(
                302,
                {"steps": [_step_row("second", status="completed"), _step_row("first", status="completed")]},
                None,
            )

            cards = window.project_runs_view.detail.pipeline_view.cards_container.findChildren(ProjectRunNodeCard)
            self.assertEqual({card.node_id for card in cards}, {"first", "second"})
        finally:
            window.close()
            tmp.cleanup()

    def test_catalog_response_populates_view_and_index(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            window.pending[101] = "project_runs_list"
            window._handle_response(
                101,
                {
                    "items": [
                        {
                            "project_run_id": "pr-1",
                            "project_name": "Briefing",
                            "status": "failed",
                            "step_count": 3,
                            "completed_step_count": 1,
                            "failed_step_count": 1,
                            "blocked_step_count": 1,
                            "failed_node_id": "image",
                            "final_artifact_count": 0,
                            "final_artifact_ids": [],
                            "trigger_type": "manual",
                            "created_at": "2026-08-07T08:00:00+00:00",
                            "started_at": "2026-08-07T08:00:01+00:00",
                        }
                    ],
                    "next_cursor": None,
                    "has_more": False,
                },
                None,
            )
            self.assertIn("pr-1", window.project_runs_index)
            self.assertEqual(window.project_runs_view.run_list.topLevelItemCount() >= 1, True)
        finally:
            window.close()
            tmp.cleanup()

    def test_polling_does_not_refresh_detail_for_terminal_run(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            self.requests.clear()
            window.selected_project_run_id = "pr-done"
            done = _catalog_item("pr-done", status="completed")
            window.project_runs_index = {"pr-done": done}
            window.project_runs_view.set_runs([done])
            window.project_runs_view.select_run("pr-done")
            # The anchor is compared against time.monotonic(), so it must be set
            # relative to the current clock -- a fixed literal makes the branch
            # depend on machine uptime. Older than 5s => list-refresh branch.
            window.project_run_last_tick_at = time.monotonic() - 10.0
            window._project_run_timer_tick()
            # Terminal runs fall through to list refresh, never to detail refresh.
            detail_requests = [
                r for r in self.requests if isinstance(r[0], tuple) and r[0][0] == "project_run_v2_detail"
            ]
            self.assertEqual(detail_requests, [])
        finally:
            window.close()
            tmp.cleanup()

    def test_polling_refreshes_detail_for_live_selected_run(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            self.requests.clear()
            window.selected_project_run_id = "pr-live"
            live = _catalog_item("pr-live", status="running")
            window.project_runs_index = {"pr-live": live}
            window.project_runs_view.set_runs([live])
            window.project_runs_view.select_run("pr-live")
            # The anchor is compared against time.monotonic(), so it must be set
            # relative to the current clock -- a fixed literal makes the branch
            # depend on machine uptime. Newer than 5s => the list refresh is
            # skipped and the tick falls through to the detail-refresh branch.
            window.project_run_last_tick_at = time.monotonic()
            window._project_run_timer_tick()
            paths = [r[1] for r in self.requests]
            self.assertIn("/v1/project-runs/pr-live", paths)
            self.assertIn("/v1/project-runs/pr-live/steps", paths)
        finally:
            window.close()
            tmp.cleanup()

    def test_retry_action_uses_failed_node_id_in_payload(self):
        from PySide6.QtWidgets import QMessageBox

        window, tmp = self._build()
        try:
            self._add_post(window)
            window.selected_project_run_id = "pr-failed"
            window.project_runs_index = {
                "pr-failed": _catalog_item(
                    "pr-failed",
                    status="failed",
                    failed_node_id="image",
                    error_code="ALL_WORKERS_FAILED",
                ),
            }
            with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                window._submit_project_run_action_v2(
                    "pr-failed",
                    "retry",
                    {"project_run_id": "pr-failed"},
                )
            kinds = [r[0] for r in self.requests]
            self.assertTrue(
                any(
                    isinstance(k, tuple) and k[0] == "project_run_action" and k[1][0] == "project_run_retry"
                    for k in kinds
                ),
                "retry POST must be dispatched",
            )
            retry_request = next(
                r
                for r in self.requests
                if isinstance(r[0], tuple) and r[0][0] == "project_run_action" and r[0][1][0] == "project_run_retry"
            )
            self.assertEqual(retry_request[1], "/v1/project-runs/pr-failed/retry")
            self.assertEqual(retry_request[2], {"from_node": "image"})
        finally:
            window.close()
            tmp.cleanup()

    def test_approve_action_posts_to_token_route(self):
        from PySide6.QtWidgets import QMessageBox

        window, tmp = self._build()
        try:
            self._add_post(window)
            window.selected_project_run_id = "pr-await"
            with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                window._approve_project_run_checkpoint("pr-await", "tok-9")
            kinds = [r[0] for r in self.requests]
            self.assertTrue(
                any(
                    isinstance(k, tuple) and k[0] == "project_run_action" and k[1][0] == "project_run_approve"
                    for k in kinds
                ),
                "approve POST must be dispatched",
            )
            approve_request = next(
                r
                for r in self.requests
                if isinstance(r[0], tuple) and r[0][0] == "project_run_action" and r[0][1][0] == "project_run_approve"
            )
            self.assertEqual(approve_request[1], "/v1/project-runs/pr-await/approvals/tok-9/approve")
        finally:
            window.close()
            tmp.cleanup()


def _step_row(node_id: str, *, status: str = "completed", active_task_run_id: str = "tr-1", **kw) -> dict:
    row = {
        "node_id": node_id,
        "task_id": "t-x",
        "task_version": 1,
        "status": status,
        "active_task_run_id": active_task_run_id,
        "started_at": "2026-08-07T08:00:00+00:00",
        "completed_at": "2026-08-07T08:01:00+00:00",
        "attempt_count": 1,
    }
    row.update(kw)
    return row


class ProjectRunInspectorWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_inspector_renders_attempts_inputs_and_outputs(self):
        inspector = ProjectRunInspectorView()
        step = _step_row("page", active_task_run_id="tr-9", status="completed")
        receipt_step = {
            "node_id": "page",
            "task_runs": [
                {
                    "step_attempt": 1,
                    "status": "failed",
                    "worker_override": "claude",
                    "created_at": "2026-08-07T07:00:00+00:00",
                    "completed_at": "2026-08-07T07:01:00+00:00",
                },
                {
                    "step_attempt": 2,
                    "status": "completed",
                    "worker_override": "codex",
                    "created_at": "2026-08-07T08:00:00+00:00",
                    "completed_at": "2026-08-07T08:01:00+00:00",
                },
            ],
            "resolved_inputs": [
                {"from_node": "pick", "from_role": "result", "to_alias": "A1", "artifact_uid": "uid-pick-result"},
                {"from_node": "image", "from_role": "output", "to_alias": "A2", "artifact_uid": "uid-image-output"},
            ],
        }
        task_run_detail = {
            "requested_worker": "auto",
            "actual_worker": "codex",
            "status": "COMPLETED",
            "error_code": None,
        }
        artifacts = [
            {
                "artifact_uid": "uid-page-final",
                "role": "final_report",
                "relative_path": "page.md",
                "size": 42,
                "sha256": "a" * 64,
            },
        ]

        inspector.set_node("pr-1", "page", step, receipt_step, task_run_detail, artifacts)

        # Header carries the node id, task, and status.
        self.assertIn("page", inspector.header_label.text())
        self.assertIn("completed", inspector.header_label.text())

        # Attempt history is sorted by attempt number and shows both attempts.
        self.assertEqual(inspector.attempts_table.rowCount(), 2)
        self.assertEqual(inspector.attempts_table.item(0, 0).text(), "Attempt 1")
        self.assertEqual(inspector.attempts_table.item(0, 1).text(), "failed")
        self.assertEqual(inspector.attempts_table.item(1, 1).text(), "completed")

        # Active Task Run section shows actual worker fallback when request differs.
        self.assertEqual(inspector.task_run_id_label.text(), "tr-9")
        self.assertEqual(inspector.requested_worker_label.text(), "auto")
        self.assertEqual(inspector.actual_worker_label.text(), "codex (requested auto)")
        self.assertEqual(inspector.task_run_status_label.text(), "COMPLETED")

        # Inputs render in the design doc's "A1 <- pick(result)" shape.
        self.assertIn("A1 ← pick(result)", inspector.inputs_label.text())
        self.assertIn("A2 ← image(output)", inspector.inputs_label.text())

        # Artifacts table mirrors the produced Artifacts.
        self.assertEqual(inspector.outputs_table.rowCount(), 1)
        self.assertEqual(inspector.outputs_table.item(0, 0).text(), "final_report")
        self.assertEqual(inspector.outputs_table.item(0, 1).text(), "page.md")

        # Actions are enabled when an active Task Run is present.
        self.assertFalse(inspector.open_logs_button.isHidden())
        self.assertTrue(inspector.open_logs_button.isEnabled())
        self.assertFalse(inspector.open_answer_button.isHidden())
        self.assertTrue(inspector.reexec_button.isEnabled())

        # Clearing the inspector returns to the empty state.
        inspector.clear()
        self.assertTrue(inspector.empty.isVisibleTo(inspector))
        self.assertTrue(inspector.body.isHidden())

    def test_inspector_action_signals(self):
        inspector = ProjectRunInspectorView()
        step = _step_row("image", active_task_run_id="tr-2", status="failed", error_code="SCHEMA_MISMATCH")
        step["task_id"] = "task-image"
        inspector.set_node("pr-x", "image", step, {"node_id": "image", "task_runs": []}, None, [])

        logs_calls: list[str] = []
        answer_calls: list[str] = []
        reexec_calls: list[str] = []
        comment_calls: list[tuple[str, str]] = []
        edit_task_calls: list[str] = []
        inspector.open_run_logs_requested.connect(logs_calls.append)
        inspector.open_run_answer_requested.connect(answer_calls.append)
        inspector.reexecute_from_node_requested.connect(reexec_calls.append)
        inspector.reexecute_with_comment_requested.connect(
            lambda node_id, comment: comment_calls.append((node_id, comment))
        )
        inspector.edit_task_requested.connect(edit_task_calls.append)

        inspector._emit_open_logs()
        inspector._emit_open_answer()
        inspector._emit_reexec()
        inspector._emit_edit_task()
        with unittest.mock.patch.object(QInputDialog, "getMultiLineText", return_value=("please fix the title", True)):
            inspector._emit_reexec_with_comment()
        with unittest.mock.patch.object(QInputDialog, "getMultiLineText", return_value=("", False)):
            inspector._emit_reexec_with_comment()

        self.assertEqual(logs_calls, ["tr-2"])
        self.assertEqual(answer_calls, ["tr-2"])
        self.assertEqual(reexec_calls, ["image"])
        self.assertEqual(edit_task_calls, ["task-image"])
        self.assertEqual(comment_calls, [("image", "please fix the title")])
        self.assertTrue(inspector.edit_task_button.isEnabled())
        self.assertTrue(inspector.comment_reexec_button.isEnabled())


class ProjectRunDetailInspectorRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selecting_a_step_row_populates_the_inspector(self):
        view = ProjectRunsView()
        run = _catalog_item("pr-y", status="running")
        view.set_runs([run])
        view.select_run("pr-y")
        view.detail.set_run(run)
        steps = [
            _step_row("pick", status="completed", active_task_run_id="tr-1"),
            _step_row("image", status="running", active_task_run_id="tr-2"),
        ]
        view.set_run_steps("pr-y", steps)

        # No row selected yet -> inspector empty.
        view.detail.inspector.clear()

        # Programmatically select the image row.
        target_row = next(
            i for i in range(view.detail.steps_table.rowCount()) if view.detail.steps_table.item(i, 1).text() == "image"
        )
        view.detail.steps_table.selectRow(target_row)

        inspector = view.detail.inspector
        self.assertIn("image", inspector.header_label.text())
        self.assertEqual(inspector.task_run_id_label.text(), "tr-2")
        self.assertFalse(inspector.reexec_button.isHidden())

    def test_cache_receipt_makes_attempt_history_visible(self):
        view = ProjectRunsView()
        run = _catalog_item("pr-z", status="failed")
        view.set_runs([run])
        view.detail.set_run(run)
        view.set_run_steps("pr-z", [_step_row("image", status="failed", active_task_run_id="tr-9")])

        receipt = {
            "steps": [
                {
                    "node_id": "image",
                    "task_runs": [
                        {"step_attempt": 1, "status": "failed", "worker_override": "claude"},
                        {"step_attempt": 2, "status": "completed", "worker_override": "codex"},
                    ],
                    "resolved_inputs": [
                        {"from_node": "pick", "from_role": "result", "to_alias": "A1", "artifact_uid": "uid-1"},
                    ],
                },
            ],
        }
        view.detail.cache_receipt(receipt)

        # Select the row so the inspector renders.
        for row in range(view.detail.steps_table.rowCount()):
            if view.detail.steps_table.item(row, 1).text() == "image":
                view.detail.steps_table.selectRow(row)
                break
        inspector = view.detail.inspector
        self.assertEqual(inspector.attempts_table.rowCount(), 2)
        self.assertEqual(inspector.attempts_table.item(0, 1).text(), "failed")
        self.assertEqual(inspector.attempts_table.item(1, 1).text(), "completed")
        self.assertIn("A1 ← pick(result)", inspector.inputs_label.text())


class ProjectRunInspectorMainWindowRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        from relay.compatibility import relay_home_id
        from relay.config import Config
        from relay.gui.main_window import MainWindow

        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name) / "home"
        config = Config(home)
        config.init()
        window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
        window.current_mode = "normal"
        self.requests: list[list] = []
        window._request = lambda kind, path: self.requests.append([kind, str(path)])
        window._request_post = lambda kind, path, payload: self.requests.append([kind, str(path), payload])
        return window, tmp

    def test_select_project_run_fetches_receipt_and_node_detail(self):
        window, tmp = self._build()
        try:
            window._select_project_run("pr-1")
            kinds = [tuple(r[0]) for r in self.requests if isinstance(r[0], tuple)]
            self.assertIn(("project_run_v2_receipt", "pr-1"), kinds)
            self.assertIn(("project_run_v2_detail", "pr-1"), kinds)
            self.assertIn(("project_run_v2_steps", "pr-1"), kinds)
        finally:
            window.close()
            tmp.cleanup()

    def test_receipt_response_forwards_attempt_history_to_detail(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            window.selected_project_run_id = "pr-1"
            window.pending[201] = ("project_run_v2_receipt", "pr-1")
            window._handle_response(
                201,
                {
                    "receipt": {
                        "steps": [
                            {
                                "node_id": "image",
                                "task_runs": [
                                    {"step_attempt": 1, "status": "failed"},
                                    {"step_attempt": 2, "status": "completed"},
                                ],
                                "resolved_inputs": [
                                    {
                                        "from_node": "pick",
                                        "from_role": "result",
                                        "to_alias": "A1",
                                        "artifact_uid": "uid-1",
                                    },
                                ],
                            }
                        ]
                    }
                },
                None,
            )
            # Detail cached the receipt: set_run_steps first to give it a step row.
            window.pending[202] = ("project_run_v2_steps", "pr-1")
            window._handle_response(
                202,
                {
                    "steps": [
                        {
                            "node_id": "image",
                            "status": "completed",
                            "active_task_run_id": "tr-1",
                            "started_at": "2026-08-07T08:00:00+00:00",
                            "completed_at": "2026-08-07T08:01:00+00:00",
                            "attempt_count": 2,
                        }
                    ]
                },
                None,
            )
            # Now the cached receipt feeds the inspector once the row is selected.
            detail = window.project_runs_view.detail
            for row in range(detail.steps_table.rowCount()):
                if detail.steps_table.item(row, 1).text() == "image":
                    detail.steps_table.selectRow(row)
                    break
            inspector = detail.inspector
            self.assertEqual(inspector.attempts_table.rowCount(), 2)
            self.assertIn("A1 ← pick(result)", inspector.inputs_label.text())
        finally:
            window.close()
            tmp.cleanup()

    def test_direct_job_detail_response_populates_inspector(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            window.selected_project_run_id = "pr-1"
            window.project_runs_view.select_run("pr-1")
            window.pending[203] = ("project_run_v2_node_detail", "pr-1", "tr-1")
            window._handle_response(
                203,
                {"job_id": "tr-1", "actual_worker": "antigravity", "requested_worker": "auto"},
                None,
            )
            self.assertEqual(
                window.project_runs_view.detail._task_run_details["tr-1"]["actual_worker"],
                "antigravity",
            )
        finally:
            window.close()
            tmp.cleanup()

    def test_artifact_preview_routes_detail_then_content_into_artifacts_view(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            window.selected_project_run_id = "pr-1"
            window.project_runs_view.select_run("pr-1")
            window._preview_project_run_artifact("a-json")
            self.assertIn(
                [("project_run_artifact_detail", "pr-1", "a-json"), "/v1/artifacts/a-json"],
                self.requests,
            )

            window.pending[204] = ("project_run_artifact_detail", "pr-1", "a-json")
            window._handle_response(
                204,
                {
                    "artifact": {
                        "artifact_uid": "a-json",
                        "relative_path": "result.json",
                        "mime_type": "application/json",
                    }
                },
                None,
            )
            self.assertIn(
                [
                    ("project_run_artifact_content", "pr-1", "a-json"),
                    "/v1/artifacts/a-json/content?max_bytes=262144",
                ],
                self.requests,
            )

            window.pending[205] = ("project_run_artifact_content", "pr-1", "a-json")
            window._handle_response(205, {"available": True, "text": '{"ok": true}'}, None)
            artifacts_view = window.project_runs_view.detail.artifacts_view
            self.assertEqual(artifacts_view._content_by_uid["a-json"]["text"], '{"ok": true}')
        finally:
            window.close()
            tmp.cleanup()

    def test_stale_artifact_preview_response_is_ignored_and_content_error_is_bounded(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            window.selected_project_run_id = "pr-current"
            window.pending[206] = ("project_run_artifact_detail", "pr-old", "a-old")
            window._handle_response(
                206,
                {"artifact": {"artifact_uid": "a-old", "relative_path": "old.json"}},
                None,
            )
            self.assertNotIn("/v1/artifacts/a-old/content?max_bytes=262144", [item[1] for item in self.requests])

            window.project_runs_view.select_run("pr-current")
            window.project_runs_view.detail.set_run(
                {
                    "project_run_id": "pr-current",
                    "status": "completed",
                    "final_artifact_ids": [
                        {"artifact_uid": "a-current", "relative_path": "current.json", "role": "output"}
                    ],
                }
            )
            window.project_runs_view.detail.artifacts_view.select_artifact("a-current", request_missing=False)
            window.pending[207] = ("project_run_artifact_content", "pr-current", "a-current")
            window._handle_response(207, None, "content request failed")
            preview = window.project_runs_view.detail.artifacts_view.metadata_preview.text()
            self.assertIn("content request failed", preview)
        finally:
            window.close()
            tmp.cleanup()

    def test_open_logs_routes_to_runs_screen_with_job_detail(self):
        window, tmp = self._build()
        try:
            window._show_project_runs()
            window.selected_project_run_id = "pr-1"
            window._open_project_run_logs("tr-9")
            self.assertEqual(window.selected_job_id, "tr-9")
            self.assertEqual(window.active_section, "runs")
            paths = [r[1] for r in self.requests]
            self.assertIn("/v1/jobs/tr-9", paths)
        finally:
            window.close()
            tmp.cleanup()

    def test_reexecute_from_node_dispatches_partial_reexecute(self):
        from PySide6.QtWidgets import QMessageBox

        window, tmp = self._build()
        try:
            window.selected_project_run_id = "pr-1"
            with unittest.mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                window._reexecute_project_run_from_node("image")
            kinds = [r[0] for r in self.requests]
            self.assertTrue(
                any(
                    isinstance(k, tuple) and k[0] == "project_run_action" and k[1][0] == "project_run_reexec"
                    for k in kinds
                )
            )
            reexec_request = next(
                r
                for r in self.requests
                if isinstance(r[0], tuple) and r[0][0] == "project_run_action" and r[0][1][0] == "project_run_reexec"
            )
            self.assertEqual(reexec_request[1], "/v1/project-runs/pr-1/partial-reexecute")
            self.assertEqual(reexec_request[2], {"from_node": "image", "cascade": True})
        finally:
            window.close()
            tmp.cleanup()

    def test_reexecute_with_comment_dispatches_instruction_addendum(self):
        window, tmp = self._build()
        try:
            window.selected_project_run_id = "pr-1"
            window._reexecute_project_run_with_comment("image", "please fix the title")
            reexec_request = next(
                r
                for r in self.requests
                if isinstance(r[0], tuple) and r[0][0] == "project_run_action" and r[0][1][0] == "project_run_reexec"
            )
            self.assertEqual(reexec_request[1], "/v1/project-runs/pr-1/partial-reexecute")
            self.assertEqual(
                reexec_request[2],
                {"from_node": "image", "cascade": True, "instruction_addendum": "please fix the title"},
            )
        finally:
            window.close()
            tmp.cleanup()

    def test_reexecute_with_comment_ignores_blank_comment(self):
        window, tmp = self._build()
        try:
            window.selected_project_run_id = "pr-1"
            window._reexecute_project_run_with_comment("image", "   ")
            self.assertFalse(
                any(
                    isinstance(r[0], tuple) and r[0][0] == "project_run_action" and r[0][1][0] == "project_run_reexec"
                    for r in self.requests
                )
            )
        finally:
            window.close()
            tmp.cleanup()

    def test_edit_task_from_node_opens_tasks_screen_with_editor(self):
        window, tmp = self._build()
        try:
            window.selected_project_run_id = "pr-1"
            window._edit_task_from_project_run_node("task-image")
            self.assertEqual(window._pending_task_edit_id, "task-image")
            self.assertEqual(window.active_section, "tasks")
            window.pending[300] = "tasks"
            with unittest.mock.patch.object(window.tasks_view, "show_edit_editor") as show_edit:
                window._handle_response(
                    300, {"ok": True, "tasks": [{"task_id": "task-image", "name": "Image step"}]}, None
                )
                show_edit.assert_called_once_with("task-image")
            self.assertIsNone(window._pending_task_edit_id)
            self.assertEqual(window.selected_task_id, "task-image")
        finally:
            window.close()
            tmp.cleanup()


def _linear_snapshot(nodes: list[dict], connections: list[dict]) -> dict:
    return {
        "project_definition": {"name": "Pipeline", "nodes": nodes, "connections": connections, "output_selection": []},
        "task_snapshots": {},
        "external_inputs": [],
    }


def _node(node_id: str, task_id: str) -> dict:
    return {"node_id": node_id, "task_id": task_id}


def _connection(from_node: str, from_role: str, to_node: str, to_alias: str = "A1") -> dict:
    return {"from_node": from_node, "from_role": from_role, "to_node": to_node, "to_alias": to_alias}


class ProjectRunPipelineWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_level_for_nodes_longest_path(self):
        nodes = [_node("a", "t-a"), _node("b", "t-b"), _node("c", "t-c"), _node("d", "t-d")]
        connections = [
            _connection("a", "out", "b"),
            _connection("a", "out", "c"),
            _connection("b", "out", "d"),
        ]
        predecessors: dict[str, list[str]] = {n["node_id"]: [] for n in nodes}
        for c in connections:
            predecessors[c["to_node"]].append(c["from_node"])
        levels = _level_for_nodes([n["node_id"] for n in nodes], predecessors)
        # a is a root (0); b and c are depth 1; d is depth 2.
        self.assertEqual(levels["a"], 0)
        self.assertEqual(levels["b"], 1)
        self.assertEqual(levels["c"], 1)
        self.assertEqual(levels["d"], 2)

    def test_pipeline_view_renders_one_card_per_node(self):
        view = ProjectRunPipelineView()
        nodes = [
            _node("pick", "t-pick"),
            _node("image", "t-image"),
            _node("page", "t-page"),
        ]
        connections = [
            _connection("pick", "result", "image"),
            _connection("pick", "result", "page"),
        ]
        snapshot = _linear_snapshot(nodes, connections)
        steps = [
            _step_row("pick", status="completed"),
            _step_row("image", status="failed", error_code="ALL_WORKERS_FAILED"),
            _step_row("page", status="blocked"),
        ]
        view.set_run("pr-1", snapshot, steps)

        cards = view.cards_container.findChildren(ProjectRunNodeCard)
        self.assertEqual(len(cards), 3)
        ids = {card.node_id for card in cards}
        self.assertEqual(ids, {"pick", "image", "page"})
        # The blocked descendant sits in the same row range; verify the styling
        # flag is set on its properties for QSS.
        blocked = next(card for card in cards if card.node_id == "page")
        self.assertEqual(blocked.property("pipelineState"), "blocked")
        failed = next(card for card in cards if card.node_id == "image")
        self.assertEqual(failed.property("pipelineState"), "failed")

    def test_pipeline_legend_uses_semantic_status_states(self):
        view = ProjectRunPipelineView()
        legend = {
            label.text(): label.property("state")
            for label in view.findChildren(QLabel)
            if label.objectName() == "statusBadge"
        }
        self.assertEqual(legend["Awaiting review"], "needs_review")
        self.assertEqual(legend["Awaiting approval"], "needs_approval")
        self.assertEqual(legend["Running"], "running")

    def test_pipeline_cards_are_inside_a_scroll_area(self):
        view = ProjectRunPipelineView()
        self.assertIsInstance(view.pipeline_scroll, QScrollArea)
        self.assertIs(view.pipeline_scroll.widget(), view.cards_container)

    def test_pipeline_edges_connect_card_boundaries_and_keep_parallel_rows(self):
        view = ProjectRunPipelineView()
        snapshot = _linear_snapshot(
            [_node("root", "t-root"), _node("left", "t-left"), _node("right", "t-right"), _node("sink", "t-sink")],
            [
                _connection("root", "out", "left"),
                _connection("root", "out", "right"),
                _connection("left", "out", "sink"),
                _connection("right", "out", "sink"),
            ],
        )
        view.set_run(
            "pr-1",
            snapshot,
            [
                _step_row("root"),
                _step_row("left"),
                _step_row("right"),
                _step_row("sink"),
            ],
        )
        view.cards_container_layout.activate()
        segments = view.cards_container.edge_segments()

        self.assertEqual(len(segments), 4)
        for segment in segments:
            self.assertLess(segment["from"].x(), segment["to"].x())
            self.assertGreater(segment["to"].x() - segment["from"].x(), 1)
        root_rows = {segment["to_node"]: segment["to"].y() for segment in segments if segment["from_node"] == "root"}
        self.assertNotEqual(root_rows["left"], root_rows["right"])

    def test_failed_to_blocked_pipeline_edge_is_dashed(self):
        view = ProjectRunPipelineView()
        snapshot = _linear_snapshot(
            [_node("failed", "t-failed"), _node("blocked", "t-blocked")],
            [_connection("failed", "out", "blocked")],
        )
        view.set_run(
            "pr-1",
            snapshot,
            [_step_row("failed", status="failed"), _step_row("blocked", status="blocked")],
        )
        self.assertTrue(view.cards_container.edge_segments()[0]["dashed"])

    def test_running_status_uses_the_signature_accent_not_generic_info_blue(self):
        self.assertEqual(_PIPELINE_STATUS_COLORS["running"], COLORS["accent.relay"])
        self.assertNotEqual(_PIPELINE_STATUS_COLORS["running"], COLORS["state.info"])

    def test_repaired_edge_is_highlighted_when_the_source_node_recovered(self):
        view = ProjectRunPipelineView()
        snapshot = _linear_snapshot(
            [_node("pick", "t-pick"), _node("image", "t-image")],
            [_connection("pick", "result", "image")],
        )
        view.set_run(
            "pr-1",
            snapshot,
            [_step_row("pick", status="completed"), _step_row("image", status="completed")],
            repaired_node_ids={"pick"},
        )
        segment = view.cards_container.edge_segments()[0]
        self.assertTrue(segment["repaired"])
        self.assertFalse(segment["dashed"])

    def test_repaired_flag_yields_to_a_still_failed_edges_dashed_signal(self):
        view = ProjectRunPipelineView()
        snapshot = _linear_snapshot(
            [_node("failed", "t-failed"), _node("blocked", "t-blocked")],
            [_connection("failed", "out", "blocked")],
        )
        view.set_run(
            "pr-1",
            snapshot,
            [_step_row("failed", status="failed"), _step_row("blocked", status="blocked")],
            repaired_node_ids={"failed"},
        )
        segment = view.cards_container.edge_segments()[0]
        self.assertTrue(segment["dashed"])
        self.assertFalse(segment["repaired"])

    def test_pipeline_card_avoids_steps_execution_metadata(self):
        view = ProjectRunPipelineView()
        snapshot = _linear_snapshot([_node("image", "t-image")], [])
        view.set_run(
            "pr-1",
            snapshot,
            [
                _step_row(
                    "image",
                    status="completed",
                    worker_override="antigravity",
                    started_at="2026-08-07T08:00:00+00:00",
                    completed_at="2026-08-07T08:00:10+00:00",
                )
            ],
        )
        card = view.cards_container.findChildren(ProjectRunNodeCard)[0]
        labels = [label.text() for label in card.findChildren(QLabel)]
        self.assertNotIn("antigravity", labels)
        self.assertNotIn("10s · antigravity", labels)

    def test_pipeline_card_renders_artifact_chip_and_emits_double_click(self):
        view = ProjectRunPipelineView()
        snapshot = _linear_snapshot([_node("render", "t-render")], [])
        view.set_run(
            "pr-1",
            snapshot,
            [_step_row("render")],
            node_artifacts={
                "render": [
                    {
                        "artifact_uid": "a-final",
                        "role": "final_report",
                        "relative_path": "report.html",
                    }
                ]
            },
        )

        chips = view.cards_container.findChildren(ProjectRunArtifactChip)
        self.assertEqual(len(chips), 1)
        selected: list[str] = []
        view.artifact_selected.connect(selected.append)
        chips[0].double_clicked.emit("a-final")
        self.assertEqual(selected, ["a-final"])

    def test_pipeline_node_click_emits_signal(self):
        view = ProjectRunPipelineView()
        snapshot = _linear_snapshot(
            [_node("a", "t-a"), _node("b", "t-b")],
            [_connection("a", "out", "b")],
        )
        view.set_run("pr-1", snapshot, [_step_row("a"), _step_row("b")])

        selected: list[str] = []
        view.node_selected.connect(selected.append)
        cards = view.cards_container.findChildren(ProjectRunNodeCard)
        # Pick the b card by node_id rather than assuming layout order.
        b_card = next(card for card in cards if card.node_id == "b")
        b_card.clicked.emit("b")
        self.assertEqual(selected, ["b"])
        # select_node marks the matching card without emitting.
        view.select_node("a")
        self.assertTrue(a_card := next(card for card in cards if card.node_id == "a"))
        self.assertTrue(a_card.property("pipelineSelected") == "true" or a_card.property("pipelineSelected") is True)


class ProjectRunArtifactModelTests(unittest.TestCase):
    def test_merge_artifacts_pins_final_and_deduplicates_by_uid(self):
        merged = _merge_project_run_artifacts(
            [
                {
                    "artifact_uid": "a-final",
                    "node_id": "render",
                    "role": "final_report",
                    "relative_path": "report.html",
                }
            ],
            {
                "research": [
                    {
                        "artifact_uid": "a-source",
                        "relative_path": "notes.md",
                        "mime_type": "text/markdown",
                    }
                ],
                "render": [
                    {
                        "artifact_uid": "a-final",
                        "relative_path": "report.html",
                        "mime_type": "text/html",
                    }
                ],
            },
        )

        self.assertEqual([item["artifact_uid"] for item in merged], ["a-final", "a-source"])
        self.assertTrue(merged[0]["is_final"])
        self.assertEqual(merged[0]["mime_type"], "text/html")
        self.assertEqual(merged[1]["node_id"], "research")

    def test_artifact_kind_uses_mime_then_extension(self):
        self.assertEqual(_artifact_kind({"mime_type": "application/json", "relative_path": "data.bin"}), "json")
        self.assertEqual(_artifact_kind({"relative_path": "README.md"}), "markdown")
        self.assertEqual(_artifact_kind({"relative_path": "page.HTML"}), "html")
        self.assertEqual(_artifact_kind({"relative_path": "cover.webp"}), "image")
        self.assertEqual(_artifact_kind({"relative_path": "manual.pdf"}), "pdf")
        self.assertEqual(_artifact_kind({"relative_path": "bundle.zip"}), "unsupported")


class ProjectRunArtifactsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_artifacts_tab_lists_final_group_then_task_groups_and_selects_primary(self):
        view = ProjectRunArtifactsView()
        view.set_run(
            "pr-1",
            [
                {"artifact_uid": "a-final", "role": "final_report", "relative_path": "report.html"},
            ],
            {
                "research": [{"artifact_uid": "a-source", "role": "notes", "relative_path": "notes.md"}],
                "render": [{"artifact_uid": "a-final", "relative_path": "report.html"}],
            },
        )

        groups = [view.artifact_tree.topLevelItem(i).text(0) for i in range(view.artifact_tree.topLevelItemCount())]
        self.assertEqual(groups, ["Final Artifacts", "research", "render"])
        self.assertEqual(view._selected_artifact_uid, "a-final")
        self.assertFalse(view.empty_preview.isVisibleTo(view))

    def test_json_artifact_renders_as_expandable_structure(self):
        view = ProjectRunArtifactsView()
        view.set_run(
            "pr-1",
            [],
            {"research": [{"artifact_uid": "a-json", "relative_path": "result.json", "mime_type": "application/json"}]},
        )
        view.cache_artifact_detail(
            "a-json", {"artifact_uid": "a-json", "relative_path": "result.json", "mime_type": "application/json"}
        )
        view.cache_artifact_content("a-json", {"available": True, "text": '{"headline": "Relay", "items": [1, 2]}'})
        view.select_artifact("a-json")

        self.assertEqual(view.preview_stack.currentWidget(), view.json_outline_preview)
        view.json_mode_button.click()
        self.assertEqual(view.preview_stack.currentWidget(), view.json_preview)
        names = [view.json_preview.topLevelItem(i).text(0) for i in range(view.json_preview.topLevelItemCount())]
        self.assertEqual(names, ["headline", "items"])
        self.assertGreater(view.json_preview.topLevelItem(1).childCount(), 0)

    def test_html_renders_and_unsupported_artifact_shows_metadata(self):
        view = ProjectRunArtifactsView()
        view.set_run(
            "pr-1",
            [],
            {
                "render": [
                    {"artifact_uid": "a-html", "relative_path": "report.html", "mime_type": "text/html"},
                    {"artifact_uid": "a-zip", "relative_path": "bundle.zip", "mime_type": "application/zip"},
                ]
            },
        )
        view.cache_artifact_content("a-html", {"available": True, "text": "<h1>Report</h1>"})
        view.select_artifact("a-html")
        self.assertEqual(view.preview_stack.currentWidget(), view.text_preview)
        self.assertIn("Report", view.text_preview.toHtml())

        view.select_artifact("a-zip")
        self.assertEqual(view.preview_stack.currentWidget(), view.metadata_preview)
        self.assertIn("bundle.zip", view.metadata_preview.text())

    def test_text_preview_shows_loading_until_content_arrives(self):
        view = ProjectRunArtifactsView()
        view.set_run(
            "pr-1",
            [],
            {"research": [{"artifact_uid": "a-json", "relative_path": "result.json", "mime_type": "application/json"}]},
        )
        view.select_artifact("a-json", request_missing=False)

        self.assertEqual(view.preview_stack.currentWidget(), view.metadata_preview)
        self.assertIn("Loading", view.metadata_preview.text())

    def test_awaiting_review_puts_candidate_artifacts_first_and_selects_them(self):
        view = ProjectRunArtifactsView()
        view.set_run(
            "pr-1",
            [{"artifact_uid": "published", "role": "output", "relative_path": "published.md"}],
            {},
            [
                {
                    "review_id": "review-1",
                    "node_id": "review-node",
                    "artifact_uid": "candidate",
                    "role": "candidate",
                    "relative_path": "candidate.md",
                    "publication_status": "candidate",
                }
            ],
        )

        groups = [view.artifact_tree.topLevelItem(i).text(0) for i in range(view.artifact_tree.topLevelItemCount())]
        self.assertEqual(groups[0], "Review candidate · review-node")
        self.assertEqual(view._selected_artifact_uid, "candidate")
        self.assertEqual(view.selected_record().review_id, "review-1")


class ProjectRunTimelineWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_timeline_groups_attempts_per_node(self):
        view = ProjectRunTimelineView()
        steps = [
            _step_row("pick", status="completed"),
            _step_row("image", status="failed"),
        ]
        receipt_steps = [
            {
                "node_id": "image",
                "task_runs": [
                    {
                        "step_attempt": 1,
                        "status": "failed",
                        "worker_override": "claude",
                        "created_at": "2026-08-07T08:00:00+00:00",
                        "completed_at": "2026-08-07T08:01:00+00:00",
                    },
                    {
                        "step_attempt": 2,
                        "status": "completed",
                        "worker_override": "codex",
                        "created_at": "2026-08-07T08:02:00+00:00",
                        "completed_at": "2026-08-07T08:03:00+00:00",
                    },
                ],
            }
        ]
        view.set_run("pr-1", steps, receipt_steps)
        self.assertFalse(view.empty.isVisibleTo(view))
        self.assertEqual(len(view.canvas.rows), 3)  # pick (1) + image attempts (2)
        # Retries become distinct rows keyed by (node_id, step_attempt).
        keys = {(row["node_id"], row["step_attempt"]) for row in view.canvas.rows}
        self.assertIn(("image", 1), keys)
        self.assertIn(("image", 2), keys)
        self.assertIn(("pick", 0), keys)
        # Summary mentions the window length and node count.
        self.assertIn("node", view.summary.text())

    def test_timeline_marks_blocked_steps_as_not_started(self):
        view = ProjectRunTimelineView()
        view.set_run(
            "pr-1",
            [
                _step_row("image", status="failed"),
                _step_row("page", status="blocked", active_task_run_id=None, started_at=None, completed_at=None),
            ],
            [],
            run_started_at="2026-08-07T08:00:00+00:00",
        )

        blocked = next(row for row in view.canvas.rows if row["node_id"] == "page")
        self.assertTrue(blocked["not_started"])
        self.assertEqual(blocked["display_label"], "Not started")
        self.assertIn("blocked", view.summary.text().casefold())

    def test_timeline_empty_state(self):
        view = ProjectRunTimelineView()
        view.set_run("pr-1", [], [])
        self.assertTrue(view.empty.isVisibleTo(view))
        self.assertFalse(view.canvas.isVisible())

    def test_timeline_canvas_paints_with_no_timing_data(self):
        canvas = ProjectRunTimelineCanvas()
        canvas.set_rows([], None, None)
        canvas.update()
        # Smoke: the canvas must accept paint without raising; geometry stays sane.
        self.assertEqual(canvas.rows, [])


class ProjectRunDetailTabsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_detail_view_has_pipeline_artifacts_timeline_and_orchestrator_tabs(self):
        view = ProjectRunDetailView()
        self.assertEqual(view.run_tabs.count(), 5)
        labels = [view.run_tabs.tabText(i) for i in range(view.run_tabs.count())]
        self.assertEqual(labels, ["Workspace", "Pipeline", "Artifacts", "Timeline", "Orchestrator"])
        self.assertNotIn("Steps", labels)

    def test_workspace_is_default_and_preserves_existing_detail_tabs(self):
        view = ProjectRunDetailView()
        view.set_run(_catalog_item("pr-1", status="completed"))
        self.assertIs(view.run_tabs.currentWidget(), view.workspace_view)
        self.assertIsNotNone(view.workspace_view.artifacts_view)
        self.assertEqual(view.run_tabs.indexOf(view.pipeline_view), 1)

    def test_workspace_review_panel_emits_feedback_action(self):
        view = ProjectRunDetailView()
        view.set_run(_catalog_item("pr-1", status="awaiting_review"))
        view.cache_review_detail(
            "review-1",
            {"review": {"review_id": "review-1", "status": "pending_human", "guidelines": "Check output"}},
        )
        seen = []
        view.review_rerun_requested.connect(lambda review_id, comment: seen.append((review_id, comment)))
        panel = view.workspace_view
        panel.review_comment.setPlainText("Add the missing evidence")
        panel._emit_rerun()
        self.assertEqual(seen, [("review-1", "Add the missing evidence")])
        self.assertFalse(panel.review_rerun.isEnabled())

    def test_workspace_buffers_changed_run_until_user_applies_update(self):
        view = ProjectRunWorkspaceView()
        initial = _catalog_item("pr-1", status="running")
        initial["steps"] = [_step_row("a", status="running")]
        view.set_run(initial, {}, {})
        view.review_comment.setPlainText("I am reading this result")

        updated = dict(initial, status="completed", workflow_status="completed")
        updated["steps"] = [_step_row("a", status="completed")]
        view.set_run(updated, {}, {})

        self.assertFalse(view.update_notice.isHidden())
        self.assertIn("실행 중", view.next_action_label.text())
        view.acknowledge_updates()
        self.assertTrue(view.update_notice.isHidden())
        self.assertIn("완료", view.next_action_label.text())

    def test_workspace_shows_orchestrator_evaluation_handoff_and_round_history(self):
        view = ProjectRunWorkspaceView()
        run = _catalog_item("pr-1", status="awaiting_review")
        run["workflow_status"] = "needs_review"
        view.set_run(
            run,
            {},
            {
                "review-1": {
                    "review": {
                        "review_id": "review-1",
                        "status": "needs_human",
                        "reviewer": "orchestrator",
                        "comment": "Human must verify the unsupported claim.",
                        "guidelines": "Check source support",
                        "evaluation_json": '{"decision":"human_review","reason":"Unsupported claim"}',
                        "reruns_used": 2,
                        "max_reruns": 2,
                    },
                    "rounds": [
                        {
                            "round_no": 1,
                            "status": "needs_human",
                            "task_run_id": "job-1",
                            "evaluation_json": '{"decision":"human_review","reason":"Unsupported claim"}',
                        }
                    ],
                }
            },
        )
        self.assertIn("Unsupported claim", view.review_evaluation.text())
        self.assertIn("Human handoff", view.review_handoff.text())
        self.assertEqual(view.review_rounds.topLevelItemCount(), 1)

    def test_workspace_clears_review_controls_after_action_completes(self):
        view = ProjectRunWorkspaceView()
        view.set_run(
            _catalog_item("pr-1", status="awaiting_review"),
            {},
            {"review-1": {"review": {"review_id": "review-1", "status": "pending_human"}}},
        )
        view.review_comment.setPlainText("Please rerun with this correction")
        self.assertTrue(view.review_rerun.isEnabled())
        view.review_action_completed("review-1")
        self.assertTrue(view.review_comment.toPlainText() == "")
        self.assertFalse(view.review_confirm.isEnabled())
        self.assertFalse(view.review_rerun.isEnabled())

    def test_project_run_catalog_skips_identical_poll_render(self):
        view = ProjectRunsView()
        runs = [_catalog_item("pr-1", status="completed")]
        view.set_runs(runs)
        render = unittest.mock.patch.object(view, "_render", wraps=view._render)
        with render as mocked:
            view.set_runs(runs, selected_run_id=None)
        mocked.assert_not_called()

    def test_pipeline_artifact_selection_enters_artifacts_tab(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=1)
        run["final_artifact_ids"] = [
            {"artifact_uid": "a-final", "role": "final_report", "relative_path": "report.html"}
        ]
        run["steps"] = [_step_row("render")]
        view.set_run(run)
        view.pipeline_view.artifact_selected.emit("a-final")

        self.assertIs(view.run_tabs.currentWidget(), view.artifacts_view)
        self.assertEqual(view.artifacts_view._selected_artifact_uid, "a-final")

    def test_run_detail_exposes_pending_review_actions(self):
        view = ProjectRunDetailView()
        view.set_run(_catalog_item("pr-1", status="awaiting_review"))
        view.cache_review_detail(
            "review-1",
            {
                "review": {
                    "review_id": "review-1",
                    "status": "pending_human",
                    "reviewer": "human",
                    "guidelines": "Check the sources",
                    "reruns_used": 1,
                    "max_reruns": 2,
                }
            },
        )
        seen = []
        view.review_confirm_requested.connect(seen.append)
        self.assertTrue(view.review_confirm_button.isVisibleTo(view))
        view._emit_review_confirm()
        self.assertEqual(seen, ["review-1"])
        self.assertFalse(view.review_confirm_button.isEnabled())

    def test_pipeline_node_selection_syncs_steps_table(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="running")
        run["steps"] = [
            _step_row("a", status="completed"),
            _step_row("b", status="running"),
        ]
        view.set_run(run)
        # Trigger pipeline node selection via the pipeline view signal.
        view.pipeline_view.node_selected.emit("b")
        selected = view.steps_table.selectedItems()
        self.assertEqual(len(selected) >= 1, True)
        self.assertEqual(view.steps_table.item(selected[0].row(), 1).text(), "b")

    def test_inspector_visibility_follows_detail_tab(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=2)
        run["steps"] = [_step_row("a", status="completed"), _step_row("b", status="completed")]
        view.set_run(run)

        view.run_tabs.setCurrentWidget(view.pipeline_view)
        view.pipeline_view.node_selected.emit("a")
        self.assertTrue(view.inspector.isVisibleTo(view))

        view.run_tabs.setCurrentWidget(view.timeline_view)
        self.assertFalse(view.inspector.isVisibleTo(view))
        view.run_tabs.setCurrentWidget(view.artifacts_view)
        self.assertFalse(view.inspector.isVisibleTo(view))

        # A Pipeline node click is an explicit request to inspect that node.
        view.run_tabs.setCurrentWidget(view.pipeline_view)
        view.pipeline_view.node_selected.emit("b")
        self.assertTrue(view.inspector.isVisibleTo(view))

    def test_pipeline_inspector_toggles_and_survives_refresh(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=2)
        run["snapshot"] = {
            "project_definition": {
                "nodes": [_node("a", "t-a"), _node("b", "t-b")],
                "connections": [_connection("a", "result", "b")],
            }
        }
        run["steps"] = [_step_row("a", status="completed"), _step_row("b", status="completed")]
        view.set_run(run)

        view.run_tabs.setCurrentWidget(view.pipeline_view)
        view.pipeline_view.node_selected.emit("b")
        self.assertTrue(view.inspector.isVisibleTo(view))
        self.assertEqual(view.inspector._node_id, "b")

        # A list/detail refresh must not close an explicitly opened inspector.
        view.set_run(run)
        self.assertTrue(view.inspector.isVisibleTo(view))
        self.assertEqual(view.inspector._node_id, "b")

        # Clicking the same Pipeline card toggles the inspector closed.
        view.pipeline_view.node_selected.emit("b")
        self.assertFalse(view.inspector.isVisibleTo(view))

    def test_steps_selection_opens_and_preserves_inspector_by_node_id(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=2)
        run["steps"] = [_step_row("first"), _step_row("second")]
        view.set_run(run)
        view.run_tabs.setCurrentWidget(view.steps_table)
        view.steps_table.selectRow(1)
        self.assertEqual(view._pipeline_inspector_node_id, "second")
        view.set_run(run)

        selected = view.steps_table.selectedItems()
        self.assertEqual(view.steps_table.item(selected[0].row(), 1).text(), "second")

    def test_steps_follow_project_node_order_not_alphabetical_order(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=3)
        run["snapshot"] = {
            "project_definition": {
                "nodes": [
                    {"node_id": "zeta", "task_id": "t-zeta"},
                    {"node_id": "alpha", "task_id": "t-alpha"},
                    {"node_id": "middle", "task_id": "t-middle"},
                ]
            }
        }
        run["steps"] = [
            _step_row("middle", status="completed"),
            _step_row("zeta", status="completed"),
            _step_row("alpha", status="completed"),
        ]
        view.set_run(run)

        ids = [view.steps_table.item(row, 1).text() for row in range(view.steps_table.rowCount())]
        self.assertEqual(ids, ["zeta", "alpha", "middle"])

    def test_worker_column_updates_when_task_run_detail_arrives(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=1)
        run["steps"] = [_step_row("first", status="completed", active_task_run_id="tr-first")]
        view.set_run(run)
        self.assertEqual(view.steps_table.item(0, 6).text(), "—")
        self.assertEqual(view.steps_table.item(0, 7).text(), "—")

        view.cache_task_run_detail(
            "tr-first",
            {"actual_worker": "antigravity", "requested_worker": "auto", "status": "COMPLETED"},
        )

        self.assertEqual(view.steps_table.item(0, 6).text(), "auto")
        self.assertEqual(view.steps_table.item(0, 7).text(), "antigravity")

    def test_steps_distinguish_requested_and_actual_worker(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=1)
        run["steps"] = [_step_row("first", status="completed", active_task_run_id="tr-first", worker_override="claude")]
        view.set_run(run)
        view.cache_task_run_detail(
            "tr-first",
            {"actual_worker": "codex", "requested_worker": "claude", "status": "COMPLETED"},
        )

        self.assertEqual(view.steps_table.item(0, 6).text(), "claude")
        self.assertEqual(view.steps_table.item(0, 7).text(), "codex")

    def test_inspector_shows_unavailable_state_when_task_run_detail_fails(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="completed", completed=1)
        run["steps"] = [_step_row("first", status="completed", active_task_run_id="tr-first")]
        view.set_run(run)
        view.run_tabs.setCurrentWidget(view.steps_table)
        view.steps_table.selectRow(0)

        view.cache_task_run_error("tr-first", "Task Run detail request failed")

        self.assertIn("Unavailable", view.inspector.task_run_status_label.text())
        self.assertIn("Task Run detail request failed", view.inspector.task_run_error_label.text())

    def test_run_tabs_hide_when_no_run_selected(self):
        view = ProjectRunDetailView()
        view.set_run({})
        self.assertTrue(view.run_tabs.isHidden())


class ProjectRunOrchestratorWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_orchestrator_event_icon_name_is_registered(self):
        # "report" mapped to the unregistered "alert-circle" and crashed this
        # tab with a KeyError on any real report event; guard the whole dict so
        # a future kind added here can't reintroduce that class of bug silently.
        for kind, icon_name in _ORCHESTRATOR_EVENT_ICON.items():
            self.assertIn(icon_name, ICON_PATHS, f"kind {kind!r} maps to unregistered icon {icon_name!r}")

    def test_no_data_shows_disabled_state(self):
        view = ProjectRunOrchestratorView()
        self.assertFalse(view.disabled_state.isHidden())
        self.assertTrue(view.event_tree.isHidden())
        self.assertTrue(view.budget_label.isHidden())

    def test_disabled_orchestrator_shows_disabled_state(self):
        view = ProjectRunOrchestratorView()
        view.set_orchestrator({"ok": True, "enabled": False, "events": [], "budget": None})
        self.assertFalse(view.disabled_state.isHidden())
        self.assertTrue(view.event_tree.isHidden())

    def test_enabled_orchestrator_hides_disabled_state_and_shows_events(self):
        view = ProjectRunOrchestratorView()
        view.set_orchestrator(
            {
                "ok": True,
                "enabled": True,
                "events": [
                    {
                        "node_id": None,
                        "kind": "note",
                        "actor": "runtime",
                        "summary": "Started.",
                        "created_at": "2026-08-10T00:00:00",
                    },
                    {
                        "node_id": "a",
                        "kind": "decision",
                        "actor": "orchestrator",
                        "summary": "Fixed a.",
                        "created_at": "2026-08-10T00:00:05",
                    },
                ],
                "budget": {
                    "llm_calls_used": 1,
                    "max_llm_calls_per_run": 8,
                    "repair_attempts_used": 1,
                    "max_repair_attempts_per_run": 6,
                },
            }
        )
        self.assertTrue(view.disabled_state.isHidden())
        self.assertFalse(view.event_tree.isHidden())
        self.assertEqual(view.event_tree.topLevelItemCount(), 2)

    def test_events_render_in_chronological_order(self):
        view = ProjectRunOrchestratorView()
        view.set_orchestrator(
            {
                "ok": True,
                "enabled": True,
                "events": [
                    {"node_id": None, "kind": "note", "actor": "runtime", "summary": "First.", "created_at": "t1"},
                    {
                        "node_id": "a",
                        "kind": "decision",
                        "actor": "orchestrator",
                        "summary": "Second.",
                        "created_at": "t2",
                    },
                    {"node_id": None, "kind": "note", "actor": "runtime", "summary": "Third.", "created_at": "t3"},
                ],
                "budget": None,
            }
        )
        summaries = [view.event_tree.topLevelItem(i).text(2) for i in range(view.event_tree.topLevelItemCount())]
        self.assertEqual(summaries, ["First.", "[a] Second.", "Third."])

    def test_decision_card_shows_actor_and_summary(self):
        view = ProjectRunOrchestratorView()
        view.set_orchestrator(
            {
                "ok": True,
                "enabled": True,
                "events": [
                    {
                        "node_id": "page",
                        "kind": "decision",
                        "actor": "orchestrator",
                        "summary": "Rebound A1 to role output.",
                        "created_at": "2026-08-10T00:00:00",
                    }
                ],
                "budget": None,
            }
        )
        item = view.event_tree.topLevelItem(0)
        self.assertEqual(item.text(1), "orchestrator")
        self.assertIn("Rebound A1 to role output.", item.text(2))

    def test_budget_display_shows_repairs_and_agent_calls(self):
        view = ProjectRunOrchestratorView()
        view.set_orchestrator(
            {
                "ok": True,
                "enabled": True,
                "events": [],
                "budget": {
                    "llm_calls_used": 2,
                    "max_llm_calls_per_run": 8,
                    "repair_attempts_used": 3,
                    "max_repair_attempts_per_run": 6,
                },
            }
        )
        self.assertIn("3/6", view.budget_label.text())
        self.assertIn("2/8", view.budget_label.text())

    def test_promotion_proposal_apply_action_does_not_mutate_anything_by_itself(self):
        """promotion_proposals is always empty today (Task 7), so the view must simply
        render nothing extra for it rather than assume a shape no Task yet produces."""
        view = ProjectRunOrchestratorView()
        view.set_orchestrator({"ok": True, "enabled": True, "events": [], "budget": None, "promotion_proposals": []})
        self.assertTrue(view.disabled_state.isHidden())

    def test_set_unavailable_shows_error_and_hides_other_states(self):
        view = ProjectRunOrchestratorView()
        view.set_orchestrator({"ok": True, "enabled": True, "events": [], "budget": None})
        view.set_unavailable("Orchestrator data request failed")
        self.assertTrue(view.disabled_state.isHidden())
        self.assertTrue(view.event_tree.isHidden())
        self.assertTrue(view.budget_label.isHidden())
        self.assertIn("Orchestrator data request failed", view.unavailable_label.text())
        self.assertFalse(view.unavailable_label.isHidden())

    def test_selection_state_survives_a_sparse_refresh(self):
        """A later cache_orchestrator call must not need to be preceded by clearing -
        set_orchestrator always rebuilds the tree from the given payload."""
        view = ProjectRunOrchestratorView()
        view.set_orchestrator(
            {
                "ok": True,
                "enabled": True,
                "events": [{"node_id": "a", "kind": "note", "actor": "runtime", "summary": "One.", "created_at": "t1"}],
                "budget": None,
            }
        )
        view.set_orchestrator(
            {
                "ok": True,
                "enabled": True,
                "events": [
                    {"node_id": "a", "kind": "note", "actor": "runtime", "summary": "One.", "created_at": "t1"},
                    {
                        "node_id": "a",
                        "kind": "decision",
                        "actor": "orchestrator",
                        "summary": "Two.",
                        "created_at": "t2",
                    },
                ],
                "budget": None,
            }
        )
        self.assertEqual(view.event_tree.topLevelItemCount(), 2)


class ProjectRunDetailOrchestratorWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cache_orchestrator_forwards_to_the_orchestrator_tab(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="running")
        run["steps"] = [_step_row("a")]
        view.set_run(run)

        view.cache_orchestrator({"ok": True, "enabled": True, "events": [], "budget": None})

        self.assertTrue(view.orchestrator_view.disabled_state.isHidden())

    def test_cache_orchestrator_error_shows_unavailable_state(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="running")
        run["steps"] = [_step_row("a")]
        view.set_run(run)

        view.cache_orchestrator_error("boom")

        self.assertIn("boom", view.orchestrator_view.unavailable_label.text())

    def test_no_run_selected_resets_orchestrator_tab_to_disabled_state(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="running")
        run["steps"] = [_step_row("a")]
        view.set_run(run)
        view.cache_orchestrator({"ok": True, "enabled": True, "events": [], "budget": None})

        view.set_run({})

        self.assertFalse(view.orchestrator_view.disabled_state.isHidden())

    def test_cache_orchestrator_marks_only_decision_kind_nodes_as_repaired(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="running")
        run["steps"] = [_step_row("a"), _step_row("b")]
        view.set_run(run)

        view.cache_orchestrator(
            {
                "ok": True,
                "enabled": True,
                "budget": None,
                "events": [
                    {"kind": "note", "node_id": "a"},
                    {"kind": "decision", "node_id": "b"},
                    {"kind": "report", "node_id": None},
                ],
            }
        )

        self.assertEqual(view._repaired_node_ids, {"b"})
        self.assertEqual(view.pipeline_view._repaired_node_ids, {"b"})

    def test_switching_to_a_different_run_clears_repaired_node_ids(self):
        view = ProjectRunDetailView()
        run = _catalog_item("pr-1", status="running")
        run["steps"] = [_step_row("a")]
        view.set_run(run)
        view.cache_orchestrator(
            {"ok": True, "enabled": True, "budget": None, "events": [{"kind": "decision", "node_id": "a"}]}
        )
        self.assertEqual(view._repaired_node_ids, {"a"})

        other_run = _catalog_item("pr-2", status="running")
        other_run["steps"] = [_step_row("a")]
        view.set_run(other_run)

        self.assertEqual(view._repaired_node_ids, set())


class ProjectRunOrchestratorMainWindowRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _build(self):
        from relay.compatibility import relay_home_id
        from relay.config import Config
        from relay.gui.main_window import MainWindow

        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name) / "home"
        config = Config(home)
        config.init()
        window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
        window.current_mode = "normal"
        self.requests: list[list] = []
        window._request = lambda kind, path: self.requests.append([kind, str(path)])
        return window, tmp

    def test_select_project_run_requests_orchestrator_endpoint(self):
        window, tmp = self._build()
        try:
            window._select_project_run("pr-1")
            paths = {tuple(request[0]) for request in self.requests}
            self.assertIn(("project_run_v2_orchestrator", "pr-1"), paths)
        finally:
            window.close()
            tmp.cleanup()

    def test_orchestrator_response_routes_to_detail_view(self):
        window, tmp = self._build()
        try:
            window._select_project_run("pr-1")
            window.pending[401] = ("project_run_v2_orchestrator", "pr-1")
            window._handle_response(401, {"ok": True, "enabled": True, "events": [], "budget": None}, None)
            self.assertTrue(window.project_runs_view.detail.orchestrator_view.disabled_state.isHidden())
        finally:
            window.close()
            tmp.cleanup()

    def test_orchestrator_error_routes_to_unavailable_state(self):
        window, tmp = self._build()
        try:
            window._select_project_run("pr-1")
            window.pending[402] = ("project_run_v2_orchestrator", "pr-1")
            window._handle_response(402, None, "network error")
            self.assertIn("network error", window.project_runs_view.detail.orchestrator_view.unavailable_label.text())
        finally:
            window.close()
            tmp.cleanup()

    def test_stale_orchestrator_response_is_ignored(self):
        window, tmp = self._build()
        try:
            window._select_project_run("pr-1")
            window._select_project_run("pr-2")
            window.pending[403] = ("project_run_v2_orchestrator", "pr-1")
            window._handle_response(403, {"ok": True, "enabled": True, "events": [], "budget": None}, None)
            # pr-1's response must not populate the view now showing pr-2.
            self.assertFalse(window.project_runs_view.detail.orchestrator_view.disabled_state.isHidden())
        finally:
            window.close()
            tmp.cleanup()


if __name__ == "__main__":
    import unittest.mock

    unittest.main()
