"""GUI regressions for the Project Runs screen (Phases 1+2).

Pins the screen behaviors called out in
``docs/Relay_GUI_Project_Runs_Screen_Design_v1.0.md`` sections 4–9:
list grouping, verdict header, steps table with attempt counts, final
artifact strip, approve/reject buttons for awaiting runs, MainWindow
routing/polling rules (no polling of terminal runs, live Run selected => 2s
detail refresh, list refresh every ~5s while the screen is open), and the
Phase 2 node inspector (attempt history, active Task Run summary, resolved
inputs as "A1 <- pick(result)", produced Artifacts, and node-level actions).
"""

from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.project_runs import (
    ProjectRunDetailView,
    ProjectRunInspectorView,
    ProjectRunsView,
    _humanize_error,
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
        self.assertEqual(detail.steps_table.columnCount(), 7)
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
        # Row 0 sorts by node_id → "image" first.
        self.assertEqual(detail.steps_table.item(0, 0).text(), "image")
        self.assertEqual(detail.steps_table.item(0, 2).text(), "2")
        self.assertEqual(detail.steps_table.item(1, 2).text(), "1")
        self.assertEqual(detail.steps_table.item(0, 4).text(), "claude")
        self.assertEqual(detail.steps_table.item(0, 6).text(), "tr-2")

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
            paths = {tuple(request[0]) for request in self.requests}
            self.assertIn(("project_run_v2_detail", "pr-1"), paths)
            self.assertIn(("project_run_v2_steps", "pr-1"), paths)
            self.assertIn(("project_run_v2_approvals", "pr-1"), paths)
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
            window.project_run_last_tick_at = 0.0  # force list-refresh branch
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
            # Anchor the list-refresh branch in the past so the tick falls through to
            # the detail-refresh branch.
            window.project_run_last_tick_at = 1_000_000.0
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
        inspector.set_node("pr-x", "image", step, {"node_id": "image", "task_runs": []}, None, [])

        logs_calls: list[str] = []
        answer_calls: list[str] = []
        reexec_calls: list[str] = []
        inspector.open_run_logs_requested.connect(logs_calls.append)
        inspector.open_run_answer_requested.connect(answer_calls.append)
        inspector.reexecute_from_node_requested.connect(reexec_calls.append)

        inspector._emit_open_logs()
        inspector._emit_open_answer()
        inspector._emit_reexec()

        self.assertEqual(logs_calls, ["tr-2"])
        self.assertEqual(answer_calls, ["tr-2"])
        self.assertEqual(reexec_calls, ["image"])


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
            i for i in range(view.detail.steps_table.rowCount()) if view.detail.steps_table.item(i, 0).text() == "image"
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
            if view.detail.steps_table.item(row, 0).text() == "image":
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
                if detail.steps_table.item(row, 0).text() == "image":
                    detail.steps_table.selectRow(row)
                    break
            inspector = detail.inspector
            self.assertEqual(inspector.attempts_table.rowCount(), 2)
            self.assertIn("A1 ← pick(result)", inspector.inputs_label.text())
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


if __name__ == "__main__":
    import unittest.mock

    unittest.main()
