"""Project Runs master/detail UI.

Project Runs are persistent execution states for Project definitions. This view
implements Phases 1, 2, 3, and 4 of the screen described in
``docs/Relay_GUI_Project_Runs_Screen_Design_v1.0.md``:

- Phase 1: master/detail with a status-driven grouping, a one-line verdict
  header, a sortable steps table, a final-artifact strip, and Approve/Reject
  actions for awaiting runs.
- Phase 2: a node inspector that exposes attempt history, the active Task Run
  summary, resolved input bindings ("A1 <- pick(result)"), produced
  Artifacts, and node-level actions (open logs, open result, re-execute
  from node).
- Phase 3: a level-based pipeline view that arranges node cards by their
  topological depth, dims blocked descendants, and dashes edges leaving
  failed steps.
- Phase 4: a timeline view that draws one bar per attempt using step
  started/completed and receipt task_runs, with separate bars for retries
  and parallel fan-outs.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .design_icons import icon
from .design_tokens import COLORS
from .design_typography import apply_type
from .design_widgets import EmptyState, LabeledButton, StatusBadge

_ERROR_HUMANIZATION: dict[str, str] = {
    "ALL_WORKERS_FAILED": "All configured workers failed for this step.",
    "SCHEMA_MISMATCH": "The Task result did not match the expected schema.",
    "PROJECT_TASK_MISSING": "The Task snapshot was missing when this step tried to dispatch.",
    "PROJECT_ARTIFACT_MISSING": "A required final Artifact was not produced.",
    "PROJECT_ARTIFACT_AMBIGUOUS": "A final-output selection matched multiple Artifacts.",
    "PROJECT_INVALID": "The Project definition was rejected by the engine.",
    "CANCELLED": "This step was cancelled.",
    "TERMINATED": "This step was terminated.",
    "SIMULATED_FAILURE": "This step failed in a synthetic scenario.",
    "WORKER_DISABLED": "The selected worker is disabled.",
    "AUTH_REQUIRED": "The worker requires authentication.",
    "DAEMON_RESTARTED": "The daemon restarted mid-Run; the step was retried.",
    "ROUTINE_VERSION_PIN_INVALID": "A pinned Routine version no longer matches.",
    "TASK_RUN_PARTIAL": "The Task finished but reported a partial result.",
}


def _humanize_error(code: str | None) -> str:
    if not code:
        return ""
    key = str(code).strip().upper()
    return _ERROR_HUMANIZATION.get(key, key.replace("_", " ").title())


def _format_duration(started_at: str | None, ended_at: str | None, *, now: str | None = None) -> str:
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    start = _parse(started_at)
    end = _parse(ended_at) or _parse(now) or datetime.now(start.tzinfo) if start else None
    if not start or not end:
        return "—"
    seconds = max(0, int((end - start).total_seconds()))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def _local_relative(value: str | None) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return str(value)[:16]
    delta = datetime.now(moment.tzinfo) - moment
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _local_date(value: str | None) -> str:
    if not value:
        return "Unknown date"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().strftime("%b %d, %Y")
    except ValueError:
        return str(value)[:10]


def _step_attempts(step: dict[str, Any]) -> list[dict[str, Any]]:
    raw = step.get("task_runs")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _verdict(run: dict[str, Any]) -> str:
    status = str(run.get("status") or "").casefold()
    if status == "completed":
        step_count = int(run.get("step_count") or 0)
        artifact_count = int(run.get("final_artifact_count") or 0)
        return f"Completed · {step_count} step{'s' if step_count != 1 else ''} · {artifact_count} final artifact{'s' if artifact_count != 1 else ''}"
    if status == "failed":
        failed_node = run.get("failed_node_id")
        blocked = int(run.get("blocked_step_count") or 0)
        error_code = run.get("error_code") or ""
        if not error_code and isinstance(run.get("warnings"), list) and run["warnings"]:
            error_code = str(run["warnings"][0].get("error_code") or "")
        reason = _humanize_error(error_code) if error_code else "no error reported"
        suffix = f" · {blocked} step{'s' if blocked != 1 else ''} blocked downstream" if blocked else ""
        target = f"Failed at step {failed_node}" if failed_node else "Failed"
        return f"{target} · {reason}{suffix}"
    if status == "awaiting_approval":
        blocked = int(run.get("blocked_step_count") or 0)
        suffix = f" · {blocked} step{'s' if blocked != 1 else ''} waiting downstream" if blocked else ""
        return f"Awaiting approval{suffix}"
    if status in {"running", "queued", "accepted"}:
        step_count = int(run.get("step_count") or 0)
        completed = int(run.get("completed_step_count") or 0)
        return f"Running · {completed}/{step_count} steps complete"
    if status == "cancelled":
        completed = int(run.get("completed_step_count") or 0)
        total = int(run.get("step_count") or 0)
        return f"Cancelled · {completed}/{total} steps reached"
    return status.replace("_", " ").title() or "Unknown"


class ProjectRunsView(QWidget):
    """Browse Project Runs and render the selected Run beside the list."""

    select_run_requested = Signal(str)
    filters_changed = Signal()
    action_requested = Signal(str, str, dict)
    open_output_requested = Signal(str)
    open_run_requested = Signal(str)
    approve_requested = Signal(str, str)
    reject_requested = Signal(str, str)
    open_run_logs_requested = Signal(str)
    open_run_answer_requested = Signal(str)
    reexecute_from_node_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runs: dict[str, dict[str, Any]] = {}
        self.selected_run_id: str | None = None
        self._tree_expanded: dict[str, bool] = {}

        root = QVBoxLayout(self)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search Project Runs, projects, agents…")
        self.search_edit.textChanged.connect(lambda _text: self._on_filters_changed())
        filters.addWidget(self.search_edit, 2)
        self.status_filter = self._combo(
            "Status", ["All", "Needs action", "Running", "Completed", "Failed", "Awaiting approval"]
        )
        self.project_filter = self._combo("Project", ["All"])
        self.trigger_filter = self._combo("Trigger", ["All", "Manual", "Schedule", "Routine"])
        self.period_filter = self._combo("Period", ["Any time", "Today", "Last 7 days", "Last 30 days"])
        for control in (self.status_filter, self.project_filter, self.trigger_filter, self.period_filter):
            control.currentIndexChanged.connect(lambda _index: self._on_filters_changed())
            filters.addWidget(control)
        root.addLayout(filters)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.run_list = QTreeWidget()
        self.run_list.setHeaderLabels(["Project Run", "Status"])
        self.run_list.setColumnWidth(0, 260)
        self.run_list.setRootIsDecorated(True)
        self.run_list.setAlternatingRowColors(True)
        self.run_list.itemClicked.connect(self._on_item_clicked)
        self.run_list.itemExpanded.connect(lambda item: self._remember_tree_state(item, True))
        self.run_list.itemCollapsed.connect(lambda item: self._remember_tree_state(item, False))
        left.addWidget(self.run_list, 1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(380)
        body.addWidget(left_widget)

        self.detail = ProjectRunDetailView()
        self.detail.action_requested.connect(self.action_requested.emit)
        self.detail.open_output_requested.connect(self.open_output_requested.emit)
        self.detail.open_run_requested.connect(self.open_run_requested.emit)
        self.detail.approve_requested.connect(self.approve_requested.emit)
        self.detail.reject_requested.connect(self.reject_requested.emit)
        self.detail.open_run_logs_requested.connect(self.open_run_logs_requested.emit)
        self.detail.open_run_answer_requested.connect(self.open_run_answer_requested.emit)
        self.detail.reexecute_from_node_requested.connect(self.reexecute_from_node_requested.emit)
        body.addWidget(self.detail, 1)
        root.addLayout(body, 1)

    @staticmethod
    def _combo(name: str, values: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(f"project_runs_{name.lower().replace(' ', '_')}")
        combo.addItems(values)
        return combo

    def filters(self) -> dict[str, str]:
        return {
            "search": self.search_edit.text().strip(),
            "status": self.status_filter.currentText(),
            "project": self.project_filter.currentText(),
            "trigger": self.trigger_filter.currentText(),
            "period": self.period_filter.currentText(),
        }

    def set_runs(
        self, runs: list[dict[str, Any]] | dict[str, dict[str, Any]], *, selected_run_id: str | None = None
    ) -> None:
        if isinstance(runs, dict):
            self.runs = {str(key): dict(value) for key, value in runs.items()}
        else:
            self.runs = {str(run.get("project_run_id")): dict(run) for run in runs if run.get("project_run_id")}
        self._refresh_project_filter()
        self.selected_run_id = selected_run_id
        self._render()

    def select_run(self, project_run_id: str | None) -> None:
        self.selected_run_id = str(project_run_id) if project_run_id else None
        self._render()

    def set_run_detail(self, project_run_id: str, detail_payload: dict[str, Any]) -> None:
        if str(project_run_id) != self.selected_run_id:
            return
        run = self.runs.setdefault(str(project_run_id), {})
        run.setdefault("project_run_id", str(project_run_id))
        if isinstance(detail_payload.get("snapshot"), dict):
            run["snapshot"] = detail_payload["snapshot"]
        if "steps" in detail_payload:
            run["steps"] = detail_payload["steps"]
        if "approvals" in detail_payload:
            run["approvals"] = detail_payload["approvals"]
        self.detail.set_run(self.runs.get(str(project_run_id), {}))

    def set_run_steps(self, project_run_id: str, steps: list[dict[str, Any]]) -> None:
        run = self.runs.setdefault(str(project_run_id), {})
        run.setdefault("project_run_id", str(project_run_id))
        run["steps"] = list(steps)
        # Preserve cached receipt data so inspector attempt history survives.
        existing_receipt_steps = self.detail._run.get("receipt_steps") if hasattr(self, "detail") else None
        self.detail.set_run(self.runs.get(str(project_run_id), {}))
        if existing_receipt_steps:
            self.detail._run["receipt_steps"] = existing_receipt_steps
            self.detail._refresh_inspector_for_current_selection()

    def set_run_approvals(self, project_run_id: str, approvals: list[dict[str, Any]]) -> None:
        run = self.runs.setdefault(str(project_run_id), {})
        run.setdefault("project_run_id", str(project_run_id))
        run["approvals"] = list(approvals)
        self.detail.set_run(self.runs.get(str(project_run_id), {}))

    def clear_selection(self) -> None:
        self.selected_run_id = None
        self.detail.set_run({})

    def has_live_run_selected(self) -> bool:
        if not self.selected_run_id:
            return False
        run = self.runs.get(self.selected_run_id) or {}
        status = str(run.get("status") or "").casefold()
        return status in {"running", "queued", "accepted", "awaiting_approval"}

    def _refresh_project_filter(self) -> None:
        current = self.project_filter.currentText()
        seen = {"All"}
        names: list[str] = []
        for run in self.runs.values():
            name = str(run.get("project_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        names.sort(key=str.casefold)
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItems(["All", *names])
        if current and current in names:
            self.project_filter.setCurrentText(current)
        self.project_filter.blockSignals(False)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        project_run_id = item.data(0, Qt.UserRole)
        if project_run_id:
            self.select_run_requested.emit(str(project_run_id))

    def _on_filters_changed(self) -> None:
        self._render()
        self.filters_changed.emit()

    def _remember_tree_state(self, item: QTreeWidgetItem, expanded: bool) -> None:
        state_key = item.data(0, Qt.UserRole + 1)
        if state_key:
            self._tree_expanded[str(state_key)] = expanded

    def _render(self) -> None:
        for index in range(self.run_list.topLevelItemCount()):
            group = self.run_list.topLevelItem(index)
            state_key = group.data(0, Qt.UserRole + 1)
            if state_key:
                self._tree_expanded[str(state_key)] = group.isExpanded()
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                state_key = child.data(0, Qt.UserRole + 1)
                if state_key:
                    self._tree_expanded[str(state_key)] = child.isExpanded()
        self.run_list.clear()

        if not self.runs:
            self.run_list.setVisible(False)
            return

        groups = (
            ("Needs action", {"failed", "awaiting_approval"}),
            ("Running", {"running", "queued", "accepted"}),
            ("Completed", {"completed"}),
            ("Cancelled", {"cancelled"}),
        )
        any_rendered = False
        for group_name, statuses in groups:
            rows = [
                run
                for run in self.runs.values()
                if str(run.get("status") or "").casefold() in statuses and self._matches_filters(run)
            ]
            rows.sort(key=lambda run: run.get("created_at") or "", reverse=True)
            if not rows:
                continue
            any_rendered = True
            group_key = f"group:{group_name}"
            header = QTreeWidgetItem([f"{group_name} · {len(rows)}", ""])
            header.setData(0, Qt.UserRole + 1, group_key)
            header.setFlags(Qt.ItemIsEnabled)
            self.run_list.addTopLevelItem(header)
            date_groups: dict[str, list[dict[str, Any]]] = {}
            for run in rows:
                date_groups.setdefault(_local_date(run.get("created_at")), []).append(run)
            for date_name, date_rows in date_groups.items():
                parent = header
                if group_name == "Completed" or group_name == "Cancelled":
                    date_group_key = f"date:{group_name}:{date_name}"
                    parent = QTreeWidgetItem([f"{date_name} · {len(date_rows)}", ""])
                    parent.setData(0, Qt.UserRole + 1, date_group_key)
                    parent.setFlags(Qt.ItemIsEnabled)
                    header.addChild(parent)
                for run in date_rows:
                    self._append_run_row(parent, run)
                if parent is not header:
                    parent.setExpanded(self._tree_expanded.get(f"date:{group_name}:{date_name}", True))
            header.setExpanded(self._tree_expanded.get(group_key, True))
        self.run_list.setVisible(any_rendered)

    def _matches_filters(self, run: dict[str, Any]) -> bool:
        filters = self.filters()
        query = filters["search"].casefold()
        haystack = " ".join(
            str(run.get(key) or "")
            for key in ("project_name", "project_id", "project_run_id", "failed_node_id", "status")
        ).casefold()
        if query and query not in haystack:
            return False
        if filters["status"] != "All":
            target = filters["status"].casefold()
            current = str(run.get("status") or "").casefold()
            allowed: set[str] = set()
            if target == "needs action":
                allowed = {"failed", "awaiting_approval"}
            elif target == "running":
                allowed = {"running", "queued", "accepted"}
            else:
                allowed = {target.replace(" ", "_")}
            if current not in allowed:
                return False
        if filters["project"] != "All" and str(run.get("project_name") or "") != filters["project"]:
            return False
        if filters["trigger"] != "All":
            trigger = str(run.get("trigger_type") or "").casefold()
            target = filters["trigger"].casefold()
            if target == "manual" and trigger not in {"manual", ""}:
                return False
            if target != "manual" and trigger != target:
                return False
        if filters["period"] != "Any time":
            days = {"Today": 0, "Last 7 days": 7, "Last 30 days": 30}[filters["period"]]
            cutoff = datetime.now().astimezone() - (
                __import__("datetime").timedelta(days=days) if days else __import__("datetime").timedelta(hours=12)
            )
            try:
                created = datetime.fromisoformat(str(run.get("created_at") or "").replace("Z", "+00:00")).astimezone()
            except ValueError:
                created = None
            if created and created < cutoff:
                return False
        return True

    def _append_run_row(self, parent: QTreeWidgetItem, run: dict[str, Any]) -> None:
        project_run_id = str(run.get("project_run_id") or "")
        project_name = str(run.get("project_name") or run.get("project_id") or "Project")
        status = str(run.get("status") or "UNKNOWN").casefold()
        step_count = int(run.get("step_count") or 0)
        completed = int(run.get("completed_step_count") or 0)
        relative = _local_relative(run.get("created_at"))
        suffix = ""
        if status == "failed" and run.get("failed_node_id"):
            suffix = f" · failed @ {run.get('failed_node_id')}"
        elif status == "awaiting_approval":
            suffix = " · awaiting"
        title = f"{project_name} · {completed}/{step_count}{suffix}"
        item = QTreeWidgetItem([title, relative])
        item.setData(0, Qt.UserRole, project_run_id)
        item.setToolTip(0, _verdict(run))
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        self._apply_status_colors(item, status)
        parent.addChild(item)
        if project_run_id and project_run_id == self.selected_run_id:
            self.run_list.setCurrentItem(item)

    @staticmethod
    def _apply_status_colors(item: QTreeWidgetItem, status: str) -> None:
        colors = {
            "completed": (COLORS["state.success"], COLORS["bg.surface"]),
            "running": (COLORS["state.info"], COLORS["bg.surface"]),
            "queued": (COLORS["state.warning"], COLORS["bg.surface"]),
            "accepted": (COLORS["state.warning"], COLORS["bg.surface"]),
            "awaiting_approval": (COLORS["state.warning"], COLORS["bg.surface"]),
            "failed": (COLORS["state.danger"], COLORS["bg.surface"]),
            "cancelled": (COLORS["text.muted"], COLORS["bg.surface"]),
        }
        if status not in colors:
            return
        foreground, background = colors[status]
        for column in range(2):
            item.setForeground(column, QColor(foreground))
            item.setBackground(column, QColor(background))


class ProjectRunDetailView(QWidget):
    """Right-hand verdict + actions + steps table + final artifact strip + node inspector."""

    action_requested = Signal(str, str, dict)
    open_output_requested = Signal(str)
    open_run_requested = Signal(str)
    approve_requested = Signal(str, str)
    reject_requested = Signal(str, str)
    open_run_logs_requested = Signal(str)
    open_run_answer_requested = Signal(str)
    reexecute_from_node_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run: dict[str, Any] = {}
        self._steps: list[dict[str, Any]] = []
        self._task_run_details: dict[str, dict[str, Any]] = {}
        self._node_artifacts: dict[str, list[dict[str, Any]]] = {}

        layout = QVBoxLayout(self)

        self.header_row = QHBoxLayout()
        self.status_badge = StatusBadge("unavailable")
        self.header_row.addWidget(self.status_badge)
        self.verdict_label = QLabel("Select a Project Run to view its overview.")
        self.verdict_label.setObjectName("projectRunVerdict")
        self.verdict_label.setWordWrap(True)
        self.header_row.addWidget(self.verdict_label, 1)

        self.actions_row = QHBoxLayout()
        self.retry_button = LabeledButton("rerun", "Retry from failure", tone="primary")
        self.retry_button.clicked.connect(lambda: self._emit_action("retry"))
        self.reexec_button = LabeledButton("play", "Re-execute from node…")
        self.reexec_button.clicked.connect(lambda: self._emit_action("reexec"))
        self.cancel_button = LabeledButton("stop", "Cancel Run", tone="danger")
        self.cancel_button.clicked.connect(lambda: self._emit_action("cancel"))
        self.output_button = LabeledButton("folder-open", "Open output folder")
        self.output_button.clicked.connect(self._emit_open_output)
        for button in (self.retry_button, self.reexec_button, self.cancel_button, self.output_button):
            self.actions_row.addWidget(button)
        self.actions_row.addStretch(1)
        self.header_row.addLayout(self.actions_row)

        layout.addLayout(self.header_row)

        self.approval_row = QHBoxLayout()
        self.approval_label = QLabel("")
        self.approval_label.setObjectName("mutedText")
        self.approval_label.setVisible(False)
        self.approval_row.addWidget(self.approval_label, 1)
        self.approve_button = LabeledButton("check-circle", "Approve", tone="primary")
        self.approve_button.clicked.connect(self._emit_approve)
        self.reject_button = LabeledButton("x-circle", "Reject", tone="danger")
        self.reject_button.clicked.connect(self._emit_reject)
        self.approval_row.addWidget(self.approve_button)
        self.approval_row.addWidget(self.reject_button)
        self.approval_row.addStretch(1)
        layout.addLayout(self.approval_row)

        self.steps_table = QTableWidget(0, 7)
        self.steps_table.setHorizontalHeaderLabels(
            ["Node", "Status", "Attempts", "Duration", "Worker", "Error", "Task Run"]
        )
        self.steps_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.steps_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.steps_table.verticalHeader().setVisible(False)
        header = self.steps_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.steps_table.itemSelectionChanged.connect(self._on_step_selection_changed)

        self.pipeline_view = ProjectRunPipelineView()
        self.pipeline_view.node_selected.connect(self._on_pipeline_node_selected)

        self.timeline_view = ProjectRunTimelineView()

        self.run_tabs = QTabWidget()
        self.run_tabs.addTab(self.pipeline_view, "Pipeline")
        self.run_tabs.addTab(self.timeline_view, "Timeline")
        self.run_tabs.addTab(self.steps_table, "Steps")
        self.run_tabs.currentChanged.connect(self._on_run_tab_changed)
        layout.addWidget(self.run_tabs, 1)

        self.inspector = ProjectRunInspectorView()
        self.inspector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.inspector)

        # Forward inspector signals to the outer detail view so MainWindow can wire them.
        self.inspector.open_run_logs_requested.connect(self.open_run_logs_requested.emit)
        self.inspector.open_run_answer_requested.connect(self.open_run_answer_requested.emit)
        self.inspector.open_artifact_requested.connect(self.open_output_requested.emit)
        self.inspector.reexecute_from_node_requested.connect(self.reexecute_from_node_requested.emit)

        self.artifact_strip = QHBoxLayout()
        self.artifact_label = QLabel("")
        self.artifact_label.setObjectName("mutedText")
        self.artifact_label.setWordWrap(True)
        self.artifact_strip.addWidget(self.artifact_label, 1)
        self.artifact_buttons_layout = QHBoxLayout()
        self.artifact_strip.addLayout(self.artifact_buttons_layout)
        self.artifact_strip.addStretch(1)
        layout.addLayout(self.artifact_strip)

        self.empty = EmptyState(
            "No Project Run selected",
            "Pick a Run from the list to see its verdict, steps, and outputs.",
            action_text="",
        )
        self.empty.setVisible(True)
        layout.addWidget(self.empty)

        self.set_run({})

    def set_run(self, run: dict[str, Any]) -> None:
        self._run = dict(run) if isinstance(run, dict) else {}
        self._steps = list(self._run.get("steps") or []) if isinstance(self._run.get("steps"), list) else []

        if not self._run.get("project_run_id"):
            self.empty.setVisible(True)
            self.status_badge.setVisible(False)
            self.verdict_label.setText("Select a Project Run to view its overview.")
            self.run_tabs.setVisible(False)
            self.artifact_label.setVisible(False)
            self._clear_action_buttons()
            self._clear_artifact_buttons()
            self.approve_button.setVisible(False)
            self.reject_button.setVisible(False)
            self.approval_label.setVisible(False)
            self.inspector.clear()
            self.pipeline_view.clear()
            self.timeline_view.clear()
            return
        self.empty.setVisible(False)
        self.status_badge.setVisible(True)
        self.run_tabs.setVisible(True)
        self.artifact_label.setVisible(True)

        status = str(self._run.get("status") or "unavailable").casefold()
        self.status_badge.set_status(status)
        self.verdict_label.setText(_verdict(self._run))

        is_failed = status == "failed"
        is_terminal = status in {"completed", "failed", "cancelled"}
        self.retry_button.setVisible(is_failed)
        self.reexec_button.setVisible(True)
        self.cancel_button.setVisible(not is_terminal)
        self.output_button.setVisible(status == "completed" and bool(self._run.get("final_artifact_ids")))

        self._render_steps()
        self._render_artifacts()
        self._render_approvals()
        self._refresh_inspector_for_current_selection()
        self._render_pipeline()
        self._render_timeline()

    def _render_steps(self) -> None:
        steps = sorted(self._steps, key=lambda item: str(item.get("node_id") or ""))
        self.steps_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            node_id = str(step.get("node_id") or "")
            status = str(step.get("status") or "unavailable").casefold()
            attempt_count = int(step.get("attempt_count") or len(_step_attempts(step)))
            duration = _format_duration(step.get("started_at"), step.get("completed_at"))
            worker = str(step.get("worker_override") or "").strip() or "—"
            attempts = _step_attempts(step)
            if attempts and not step.get("worker_override"):
                worker = str(attempts[-1].get("worker_override") or "").strip() or "—"
            error_code = str(step.get("error_code") or "").strip()
            error_text = _humanize_error(error_code) if error_code else "—"
            task_run_id = str(step.get("active_task_run_id") or "")

            values = [node_id, status.title(), str(attempt_count), duration, worker, error_text, task_run_id]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 6 and value:
                    item.setData(Qt.UserRole, value)
                    item.setForeground(QColor(COLORS["accent.primary"]))
                item.setToolTip(value)
                self.steps_table.setItem(row, column, item)

    def _render_pipeline(self) -> None:
        snapshot = self._run.get("snapshot")
        self.pipeline_view.set_run(
            str(self._run.get("project_run_id") or ""),
            snapshot if isinstance(snapshot, dict) else None,
            self._steps,
            self._run.get("receipt_steps") or [],
        )

    def _render_timeline(self) -> None:
        self.timeline_view.set_run(
            str(self._run.get("project_run_id") or ""),
            self._steps,
            self._run.get("receipt_steps") or [],
        )

    def _on_pipeline_node_selected(self, node_id: str) -> None:
        for row in range(self.steps_table.rowCount()):
            item = self.steps_table.item(row, 0)
            if item and item.text() == node_id:
                self.steps_table.selectRow(row)
                return

    def _on_run_tab_changed(self, index: int) -> None:
        if index == self.run_tabs.indexOf(self.pipeline_view):
            # Keep pipeline selection synced with the steps table / inspector.
            items = self.steps_table.selectedItems()
            if items:
                row = items[0].row()
                item = self.steps_table.item(row, 0)
                if item:
                    self.pipeline_view.select_node(item.text())

    def _render_artifacts(self) -> None:
        self._clear_artifact_buttons()
        final = self._run.get("final_artifact_ids")
        items = [item for item in (final or []) if isinstance(item, dict)] if isinstance(final, list) else []
        if not items:
            self.artifact_label.setText("No final artifacts yet.")
            return
        label_parts = []
        for entry in items:
            role = str(entry.get("role") or "output")
            node = str(entry.get("node_id") or "")
            label_parts.append(f"{role} ({node})" if node else role)
        self.artifact_label.setText(f"Final artifacts: {', '.join(label_parts)}")
        for entry in items:
            artifact_uid = str(entry.get("artifact_uid") or "")
            if not artifact_uid:
                continue
            button = QToolButton()
            button.setText(str(entry.get("role") or "output"))
            button.setIcon(icon("external-link", "default"))
            button.setToolTip(f"Open {entry.get('role') or 'output'}")
            button.clicked.connect(lambda _checked=False, uid=artifact_uid: self.open_output_requested.emit(uid))
            self.artifact_buttons_layout.addWidget(button)

    def _on_step_selection_changed(self) -> None:
        self._refresh_inspector_for_current_selection()

    def _refresh_inspector_for_current_selection(self) -> None:
        items = self.steps_table.selectedItems()
        if not items:
            self.inspector.clear()
            return
        row = items[0].row()
        node_id_item = self.steps_table.item(row, 0)
        node_id = str(node_id_item.text() if node_id_item else "")
        if not node_id:
            self.inspector.clear()
            return
        step = next((s for s in self._steps if str(s.get("node_id")) == node_id), {})
        receipt_step = self._find_receipt_step(node_id)
        task_run_id = str(step.get("active_task_run_id") or "")
        cached = self._task_run_details.get(task_run_id) if task_run_id else None
        artifacts = self._node_artifacts.get(node_id) or []
        self.inspector.set_node(
            str(self._run.get("project_run_id") or ""),
            node_id,
            step,
            receipt_step,
            cached,
            artifacts,
        )

    def _find_receipt_step(self, node_id: str) -> dict[str, Any]:
        steps = self._run.get("receipt_steps") or []
        for entry in steps:
            if isinstance(entry, dict) and str(entry.get("node_id")) == node_id:
                return entry
        return {}

    def cache_receipt(self, receipt: dict[str, Any]) -> None:
        if not isinstance(receipt, dict):
            return
        steps = receipt.get("steps") or []
        if isinstance(steps, list):
            self._run["receipt_steps"] = [s for s in steps if isinstance(s, dict)]
        self._refresh_inspector_for_current_selection()

    def cache_task_run_detail(self, task_run_id: str, detail: dict[str, Any]) -> None:
        self._task_run_details[str(task_run_id)] = dict(detail) if isinstance(detail, dict) else {}
        self._refresh_inspector_for_current_selection()

    def cache_node_artifacts(self, node_id: str, artifacts: list[dict[str, Any]]) -> None:
        self._node_artifacts[str(node_id)] = [item for item in artifacts if isinstance(item, dict)]
        self._refresh_inspector_for_current_selection()

    def _render_approvals(self) -> None:
        status = str(self._run.get("status") or "").casefold()
        approvals = self._run.get("approvals") or []
        awaiting = status == "awaiting_approval"
        has_pending = any(
            str(item.get("status") or "").casefold() == "pending" for item in approvals if isinstance(item, dict)
        )
        self.approve_button.setVisible(awaiting and has_pending)
        self.reject_button.setVisible(awaiting and has_pending)
        if awaiting:
            pending = [
                item
                for item in approvals
                if isinstance(item, dict) and str(item.get("status") or "").casefold() == "pending"
            ]
            if pending:
                token = str(pending[0].get("token") or "")
                self.approval_label.setText(f"Awaiting approval · token {token[:8]}")
                self.approval_label.setVisible(True)
                self._pending_token = token
                self._pending_node_id = str(pending[0].get("node_id") or "")
                return
        self.approval_label.setVisible(False)
        self._pending_token = ""
        self._pending_node_id = ""

    def _emit_action(self, action: str) -> None:
        project_run_id = str(self._run.get("project_run_id") or "")
        if not project_run_id:
            return
        self.action_requested.emit(project_run_id, action, {"project_run_id": project_run_id})

    def _emit_open_output(self) -> None:
        final = self._run.get("final_artifact_ids") or []
        items = [item for item in final if isinstance(item, dict)]
        if not items:
            return
        first = items[0]
        artifact_uid = str(first.get("artifact_uid") or "")
        if artifact_uid:
            self.open_output_requested.emit(artifact_uid)

    def _emit_approve(self) -> None:
        token = getattr(self, "_pending_token", "")
        project_run_id = str(self._run.get("project_run_id") or "")
        if token and project_run_id:
            self.approve_requested.emit(project_run_id, token)

    def _emit_reject(self) -> None:
        token = getattr(self, "_pending_token", "")
        project_run_id = str(self._run.get("project_run_id") or "")
        if token and project_run_id:
            self.reject_requested.emit(project_run_id, token)

    def _clear_action_buttons(self) -> None:
        for button in (self.retry_button, self.reexec_button, self.cancel_button, self.output_button):
            button.setVisible(False)

    def _clear_artifact_buttons(self) -> None:
        while self.artifact_buttons_layout.count():
            item = self.artifact_buttons_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()


def _format_attempt_label(step_attempt: int | None) -> str:
    if step_attempt is None:
        return "Attempt"
    return f"Attempt {int(step_attempt)}"


class ProjectRunInspectorView(QWidget):
    """Per-node detail panel for a selected Project Run.

    Renders five sections described in design doc §5 (Node Inspector):
    attempt history, active Task Run summary, resolved inputs ("A1 <- pick(result)"),
    produced Artifacts, and node-level actions (open logs, open result,
    re-execute from node).  All sections are filled from the daemon
    responses that ``ProjectRunsView`` already loads; this widget holds no
    network state of its own.
    """

    open_run_logs_requested = Signal(str)
    open_run_answer_requested = Signal(str)
    open_artifact_requested = Signal(str)
    reexecute_from_node_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_run_id: str | None = None
        self._node_id: str | None = None
        self._step: dict[str, Any] = {}
        self._receipt_step: dict[str, Any] = {}
        self._task_run_detail: dict[str, Any] | None = None
        self._artifacts: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)

        self.header_label = QLabel("Select a step to inspect.")
        self.header_label.setObjectName("sectionTitle")
        apply_type(self.header_label, "title.section")
        self.header_label.setWordWrap(True)
        layout.addWidget(self.header_label)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)

        body_layout.addWidget(self._build_attempts_section())
        body_layout.addWidget(self._build_task_run_section())
        body_layout.addWidget(self._build_inputs_section())
        body_layout.addWidget(self._build_outputs_section())
        body_layout.addWidget(self._build_actions_section())
        body_layout.addStretch(1)
        layout.addWidget(self.body, 1)

        self.empty = EmptyState(
            "No step selected",
            "Click a row in the steps table above to see that step's attempt history, inputs, outputs, and actions.",
            action_text="",
        )
        layout.addWidget(self.empty)
        self._set_empty(True)

    def _set_empty(self, empty: bool) -> None:
        self.body.setVisible(not empty)
        self.empty.setVisible(empty)
        self.header_label.setVisible(not empty or empty)

    def _build_attempts_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("inspectorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Attempt history")
        title.setObjectName("sectionTitle")
        apply_type(title, "overline")
        layout.addWidget(title)
        self.attempts_table = QTableWidget(0, 5)
        self.attempts_table.setHorizontalHeaderLabels(["Attempt", "Status", "Worker", "Started", "Completed"])
        self.attempts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.attempts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.attempts_table.verticalHeader().setVisible(False)
        header = self.attempts_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.attempts_table.setMinimumHeight(110)
        layout.addWidget(self.attempts_table)
        return section

    def _build_task_run_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("inspectorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Active Task Run")
        title.setObjectName("sectionTitle")
        apply_type(title, "overline")
        layout.addWidget(title)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.task_run_id_label = QLabel("—")
        self.requested_worker_label = QLabel("—")
        self.actual_worker_label = QLabel("—")
        self.task_run_status_label = QLabel("—")
        self.task_run_error_label = QLabel("—")
        for label in (
            self.task_run_id_label,
            self.requested_worker_label,
            self.actual_worker_label,
            self.task_run_status_label,
            self.task_run_error_label,
        ):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Task Run", self.task_run_id_label)
        form.addRow("Requested worker", self.requested_worker_label)
        form.addRow("Actual worker", self.actual_worker_label)
        form.addRow("Status", self.task_run_status_label)
        form.addRow("Error", self.task_run_error_label)
        layout.addLayout(form)
        return section

    def _build_inputs_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("inspectorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Inputs")
        title.setObjectName("sectionTitle")
        apply_type(title, "overline")
        layout.addWidget(title)
        self.inputs_label = QLabel("No inputs recorded.")
        self.inputs_label.setObjectName("mutedText")
        self.inputs_label.setWordWrap(True)
        self.inputs_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.inputs_label)
        return section

    def _build_outputs_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("inspectorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Artifacts produced")
        title.setObjectName("sectionTitle")
        apply_type(title, "overline")
        layout.addWidget(title)
        self.outputs_table = QTableWidget(0, 4)
        self.outputs_table.setHorizontalHeaderLabels(["Role", "File", "Size", "Sha256"])
        self.outputs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.outputs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.outputs_table.verticalHeader().setVisible(False)
        header = self.outputs_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.outputs_table.setMinimumHeight(110)
        layout.addWidget(self.outputs_table)
        return section

    def _build_actions_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("inspectorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Actions")
        title.setObjectName("sectionTitle")
        apply_type(title, "overline")
        layout.addWidget(title)
        actions = QHBoxLayout()
        self.open_logs_button = LabeledButton("file-text", "Open logs")
        self.open_logs_button.clicked.connect(self._emit_open_logs)
        self.open_answer_button = LabeledButton("external-link", "Open answer")
        self.open_answer_button.clicked.connect(self._emit_open_answer)
        self.reexec_button = LabeledButton("play", "Re-execute from this node")
        self.reexec_button.clicked.connect(self._emit_reexec)
        actions.addWidget(self.open_logs_button)
        actions.addWidget(self.open_answer_button)
        actions.addWidget(self.reexec_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return section

    def clear(self) -> None:
        self._project_run_id = None
        self._node_id = None
        self._step = {}
        self._receipt_step = {}
        self._task_run_detail = None
        self._artifacts = []
        self._set_empty(True)
        self.header_label.setText("Select a step to inspect.")

    def set_node(
        self,
        project_run_id: str,
        node_id: str,
        step: dict[str, Any],
        receipt_step: dict[str, Any] | None,
        task_run_detail: dict[str, Any] | None,
        artifacts: list[dict[str, Any]],
    ) -> None:
        self._project_run_id = project_run_id
        self._node_id = node_id
        self._step = dict(step) if isinstance(step, dict) else {}
        self._receipt_step = dict(receipt_step) if isinstance(receipt_step, dict) else {}
        self._task_run_detail = dict(task_run_detail) if isinstance(task_run_detail, dict) else None
        self._artifacts = [item for item in artifacts if isinstance(item, dict)]
        self._render()

    def _render(self) -> None:
        if not self._node_id:
            self._set_empty(True)
            return
        self._set_empty(False)

        task_label = self._step.get("task_id") or ""
        status = str(self._step.get("status") or "unavailable")
        node_label = f"{self._node_id} · task {task_label} · {status}" if task_label else f"{self._node_id} · {status}"
        self.header_label.setText(node_label)

        self._render_attempts()
        self._render_task_run()
        self._render_inputs()
        self._render_outputs()
        self._render_actions()

    def _render_attempts(self) -> None:
        attempts = list(self._receipt_step.get("task_runs") or [])
        if not attempts:
            attempts = [
                {
                    "step_attempt": 1,
                    "worker_override": self._step.get("worker_override"),
                    "status": self._step.get("status"),
                    "created_at": self._step.get("started_at"),
                    "completed_at": self._step.get("completed_at"),
                }
            ]
        attempts = sorted(attempts, key=lambda item: int(item.get("step_attempt") or 0))
        self.attempts_table.setRowCount(len(attempts))
        for row, attempt in enumerate(attempts):
            attempt_value = attempt.get("step_attempt")
            status = str(attempt.get("status") or "").casefold()
            worker = str(attempt.get("worker_override") or "").strip() or "—"
            started = str(attempt.get("created_at") or "")[:19]
            completed = str(attempt.get("completed_at") or "")[:19]
            values = [_format_attempt_label(attempt_value), status, worker, started or "—", completed or "—"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.attempts_table.setItem(row, column, item)

    def _render_task_run(self) -> None:
        detail = self._task_run_detail
        active_task_run_id = str(self._step.get("active_task_run_id") or "")
        if not active_task_run_id:
            self.task_run_id_label.setText("—")
            self.requested_worker_label.setText("—")
            self.actual_worker_label.setText("—")
            self.task_run_status_label.setText("—")
            self.task_run_error_label.setText("—")
            return
        self.task_run_id_label.setText(active_task_run_id)
        if detail:
            requested = str(detail.get("requested_worker") or "").strip() or "—"
            actual = str(detail.get("actual_worker") or "").strip()
            status = str(detail.get("status") or "").strip() or "—"
            error = str(detail.get("error_code") or "").strip()
            if not actual or actual.lower() == requested.lower():
                actual_label = actual or requested
            else:
                actual_label = f"{actual} (requested {requested})"
            self.requested_worker_label.setText(requested)
            self.actual_worker_label.setText(actual_label)
            self.task_run_status_label.setText(status)
            self.task_run_error_label.setText(error or "—")
        else:
            self.requested_worker_label.setText("Loading…")
            self.actual_worker_label.setText("Loading…")
            self.task_run_status_label.setText("Loading…")
            self.task_run_error_label.setText("Loading…")

    def _render_inputs(self) -> None:
        inputs = self._receipt_step.get("resolved_inputs") or self._step.get("input_manifest_json")
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs or "[]")
            except (TypeError, json.JSONDecodeError):
                inputs = []
        if not isinstance(inputs, list) or not inputs:
            self.inputs_label.setText("No inputs recorded.")
            return
        parts: list[str] = []
        for entry in inputs:
            if not isinstance(entry, dict):
                continue
            alias = str(entry.get("to_alias") or "")
            from_node = str(entry.get("from_node") or "").strip()
            from_role = str(entry.get("from_role") or "").strip()
            artifact_uid = str(entry.get("artifact_uid") or "").strip()
            if from_node and from_role:
                line = f"{alias} ← {from_node}({from_role})"
            elif alias:
                line = f"{alias} ← external input"
            else:
                line = alias or "(unlabeled input)"
            if artifact_uid:
                line += f" · {artifact_uid[:12]}"
            parts.append(line)
        self.inputs_label.setText("\n".join(parts) if parts else "No inputs recorded.")

    def _render_outputs(self) -> None:
        artifacts = self._artifacts
        self.outputs_table.setRowCount(len(artifacts))
        for row, artifact in enumerate(artifacts):
            role = str(artifact.get("role") or "output")
            name = str(artifact.get("relative_path") or artifact.get("name") or "—")
            size = artifact.get("size")
            size_text = str(size) if size is not None else "—"
            sha = str(artifact.get("sha256") or "")[:12] or "—"
            values = [role, name, size_text, sha]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0 and str(artifact.get("artifact_uid") or ""):
                    item.setData(Qt.UserRole, str(artifact.get("artifact_uid")))
                self.outputs_table.setItem(row, column, item)

    def _render_actions(self) -> None:
        active_task_run_id = str(self._step.get("active_task_run_id") or "")
        status = str(self._step.get("status") or "").casefold()
        # The buttons stay visible but disabled when no active Task Run exists.
        self.open_logs_button.setEnabled(bool(active_task_run_id))
        self.open_answer_button.setEnabled(bool(active_task_run_id) and status in {"completed", "partial", "failed"})
        self.reexec_button.setEnabled(bool(self._project_run_id and self._node_id))

    def _emit_open_logs(self) -> None:
        active_task_run_id = str(self._step.get("active_task_run_id") or "")
        if active_task_run_id:
            self.open_run_logs_requested.emit(active_task_run_id)

    def _emit_open_answer(self) -> None:
        active_task_run_id = str(self._step.get("active_task_run_id") or "")
        if active_task_run_id:
            self.open_run_answer_requested.emit(active_task_run_id)

    def _emit_reexec(self) -> None:
        if self._project_run_id and self._node_id:
            self.reexecute_from_node_requested.emit(self._node_id)


# --- Phase 3 (pipeline view) and Phase 4 (timeline view) helpers --------------


_PIPELINE_STATUS_COLORS: dict[str, str] = {
    "completed": COLORS["state.success"],
    "running": COLORS["state.info"],
    "queued": COLORS["state.warning"],
    "accepted": COLORS["state.warning"],
    "awaiting_approval": COLORS["state.warning"],
    "failed": COLORS["state.danger"],
    "blocked": COLORS["text.muted"],
    "cancelled": COLORS["text.muted"],
}


def _level_for_nodes(node_ids: list[str], predecessors: dict[str, list[str]]) -> dict[str, int]:
    """Assign a topological level to every node id (longest-path from any root)."""
    levels: dict[str, int] = {nid: 0 for nid in node_ids}
    for nid in node_ids:
        visited: set[str] = set()
        stack: list[str] = [nid]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for pred_id in predecessors.get(current, []):
                candidate = levels.get(pred_id, 0) + 1
                if candidate > levels.get(current, 0):
                    levels[current] = candidate
                stack.append(pred_id)
    return levels


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class ProjectRunPipelineView(QWidget):
    """Level-based DAG view described in design doc §5 (Pipeline view).

    Arranges node cards in columns by their topological depth. Blocked
    descendants of failed steps render dimmed with a dashed border, and
    edges leaving failed steps render dashed so the cause/effect is visible
    at a glance. Clicking a node card emits ``node_selected`` for the
    parent detail widget to feed into the inspector.
    """

    node_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_run_id: str | None = None
        self._nodes: list[dict[str, Any]] = []
        self._connections: list[dict[str, Any]] = []
        self._steps_by_id: dict[str, dict[str, Any]] = {}
        self._receipt_steps_by_id: dict[str, dict[str, Any]] = {}

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.addWidget(self._build_legend())
        self.cards_container = QWidget()
        self.cards_container_layout = QGridLayout(self.cards_container)
        self.cards_container_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_container_layout.setSpacing(24)
        self.body_layout.addWidget(self.cards_container, 1)
        self._root_layout.addWidget(self.body, 1)

        self.empty = EmptyState(
            "Pipeline unavailable",
            "The selected Project Run has no nodes yet.",
            action_text="",
        )
        self.empty.setVisible(False)
        self._root_layout.addWidget(self.empty)

    def _build_legend(self) -> QWidget:
        row = QHBoxLayout()
        legend = QFrame()
        legend.setObjectName("mutedText")
        legend_layout = QHBoxLayout(legend)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        for label in ("Completed", "Running", "Failed", "Blocked", "Awaiting approval", "Cancelled"):
            chip = QLabel(label)
            apply_type(chip, "caption")
            chip.setProperty("state", _PIPELINE_STATUS_COLORS.get(label.lower().replace(" ", "_"), "unavailable"))
            legend_layout.addWidget(chip)
        row.addWidget(legend)
        row.addStretch(1)
        container = QWidget()
        container.setLayout(row)
        return container

    def clear(self) -> None:
        self._project_run_id = None
        self._nodes = []
        self._connections = []
        self._steps_by_id = {}
        self._receipt_steps_by_id = {}
        self._render()

    def set_run(
        self,
        project_run_id: str | None,
        snapshot: dict[str, Any] | None,
        steps: list[dict[str, Any]],
        receipt_steps: list[dict[str, Any]] | None = None,
    ) -> None:
        self._project_run_id = project_run_id
        definition: dict[str, Any] = {}
        if isinstance(snapshot, dict):
            inner = snapshot.get("project_definition")
            if isinstance(inner, dict):
                definition = inner
        nodes_raw = definition.get("nodes") or []
        connections_raw = definition.get("connections") or []
        if not isinstance(nodes_raw, list):
            nodes_raw = []
        if not isinstance(connections_raw, list):
            connections_raw = []
        self._nodes = [n for n in nodes_raw if isinstance(n, dict)]
        self._connections = [c for c in connections_raw if isinstance(c, dict)]
        self._steps_by_id = {str(s.get("node_id") or ""): s for s in steps if isinstance(s, dict)}
        self._receipt_steps_by_id = {
            str(r.get("node_id") or ""): r for r in (receipt_steps or []) if isinstance(r, dict)
        }
        self._render()

    def select_node(self, node_id: str) -> None:
        """Programmatically highlight a node card (does not emit a signal)."""
        for child in self.cards_container.findChildren(ProjectRunNodeCard):
            child.set_selected(child.node_id == node_id)

    def _render(self) -> None:
        # Clear previous cards and their edges.
        while self.cards_container_layout.count():
            item = self.cards_container_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if not self._nodes:
            self.empty.setVisible(True)
            self.body.setVisible(False)
            return
        self.empty.setVisible(False)
        self.body.setVisible(True)

        node_ids = [str(n.get("node_id") or "") for n in self._nodes]
        predecessors: dict[str, list[str]] = {nid: [] for nid in node_ids}
        for conn in self._connections:
            from_node = str(conn.get("from_node") or "")
            to_node = str(conn.get("to_node") or "")
            if to_node in predecessors:
                predecessors[to_node].append(from_node)
        levels = _level_for_nodes(node_ids, predecessors)
        # Group nodes by level.
        by_level: dict[int, list[str]] = {}
        for nid in node_ids:
            by_level.setdefault(levels.get(nid, 0), []).append(nid)
        max_level = max(by_level.keys()) if by_level else 0

        # Reserve a column for each level; rows = node position within the column.
        positions: dict[str, tuple[int, int]] = {}
        for level in range(max_level + 1):
            members = sorted(by_level.get(level, []))
            for row, nid in enumerate(members):
                positions[nid] = (level, row)

        # Determine failed nodes so blocked descendants can dim + edges can dash.
        # (status is read directly from per-step dicts when computing edge styles
        # below; no separate index is needed here.)

        # Build cards first, then compute edge overlay positions.
        for nid in node_ids:
            level, row = positions[nid]
            node_def = next((n for n in self._nodes if str(n.get("node_id") or "") == nid), {})
            step = self._steps_by_id.get(nid, {})
            card = ProjectRunNodeCard(nid, node_def, step, self._receipt_steps_by_id.get(nid))
            card.clicked.connect(self._on_card_clicked)
            self.cards_container_layout.addWidget(card, row, level)

        # Edges: draw after layout so cards have a position. We use a simple overlay
        # label per column pair to keep the implementation free of custom paint.
        for conn in self._connections:
            from_node = str(conn.get("from_node") or "")
            to_node = str(conn.get("to_node") or "")
            if from_node not in positions or to_node not in positions:
                continue
            from_pos = positions[from_node]
            to_pos = positions[to_node]
            from_step = self._steps_by_id.get(from_node, {})
            to_step = self._steps_by_id.get(to_node, {})
            dashed = (
                str(from_step.get("status") or "").casefold() == "failed"
                or str(to_step.get("status") or "").casefold() == "blocked"
            )
            edge = ProjectRunEdgeArrow(from_pos, to_pos, dashed=dashed)
            self.cards_container_layout.addWidget(edge, 0, 0, max_level + 1, max_level + 1)

    def _on_card_clicked(self, node_id: str) -> None:
        self.select_node(node_id)
        self.node_selected.emit(node_id)


class ProjectRunNodeCard(QFrame):
    """Compact node card: icon + node_id + task name + duration/worker + retry badge + error."""

    clicked = Signal(str)

    def __init__(
        self,
        node_id: str,
        node_def: dict[str, Any],
        step: dict[str, Any],
        receipt_step: dict[str, Any] | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.node_id = node_id
        self.setObjectName("pipelineNodeCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(150)
        self.setMaximumWidth(220)

        status = str(step.get("status") or "queued").casefold()
        attempts: list[dict[str, Any]] = []
        if isinstance(receipt_step, dict):
            raw = receipt_step.get("task_runs") or []
            if isinstance(raw, list):
                attempts = [item for item in raw if isinstance(item, dict)]
        attempts = sorted(attempts, key=lambda item: int(item.get("step_attempt") or 0))
        attempt_count = int(step.get("attempt_count") or len(attempts) or 1)
        error_code = str(step.get("error_code") or "").strip()

        worker = str(step.get("worker_override") or "").strip()
        if not worker and attempts:
            worker = str(attempts[-1].get("worker_override") or "").strip()
        worker = worker or "—"

        duration = _format_duration(step.get("started_at"), step.get("completed_at"))
        task_label = str(node_def.get("task_id") or step.get("task_id") or "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(6)
        status_icon = QLabel()
        status_icon.setPixmap(icon(_pipeline_icon_name(status), "default").pixmap(14, 14))
        header.addWidget(status_icon)
        node_label = QLabel(node_id)
        node_label.setObjectName("pipelineNodeId")
        apply_type(node_label, "body.strong")
        header.addWidget(node_label, 1)
        if attempt_count > 1:
            badge = QLabel(f"retry {attempt_count - 1}")
            apply_type(badge, "caption")
            badge.setObjectName("pipelineRetryBadge")
            header.addWidget(badge)
        layout.addLayout(header)

        task_name = QLabel(task_label)
        task_name.setObjectName("mutedText")
        task_name.setWordWrap(True)
        apply_type(task_name, "caption")
        layout.addWidget(task_name)

        meta = QLabel(f"{duration} · {worker}")
        meta.setObjectName("mutedText")
        apply_type(meta, "caption")
        layout.addWidget(meta)

        if status == "failed" and error_code:
            err_label = QLabel(_humanize_error(error_code))
            apply_type(err_label, "caption")
            err_label.setWordWrap(True)
            err_label.setObjectName("pipelineErrorCode")
            layout.addWidget(err_label)

        # Visual rules: status tint (color + dashed border) per design doc §5.
        color = _PIPELINE_STATUS_COLORS.get(status, COLORS["text.muted"])
        self.setProperty("pipelineState", status)
        self.setProperty("pipelineColor", color)
        if status == "blocked":
            # "Blocked" reads as "did not run", distinct from "Failed": dimmed +
            # dashed border.
            self.setStyleSheet(
                f'QFrame#pipelineNodeCard[pipelineState="blocked"]'
                f"{{ border: 1px dashed {COLORS['text.muted']}; background: {COLORS['bg.surface']}; }}"
            )
        else:
            self.setStyleSheet(
                f'QFrame#pipelineNodeCard[pipelineState="{status}"]'
                f"{{ border: 1px solid {color}; background: {COLORS['bg.surface']}; }}"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self.clicked.emit(self.node_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("pipelineSelected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


def _pipeline_icon_name(status: str) -> str:
    return {
        "completed": "check-circle",
        "failed": "alert-triangle",
        "running": "activity",
        "blocked": "x-circle",
        "awaiting_approval": "info",
        "cancelled": "x-circle",
        "queued": "dot",
        "accepted": "dot",
    }.get(status, "dot")


class ProjectRunEdgeArrow(QWidget):
    """Lightweight edge between two grid cells. Draws a dashed line if requested.

    Implemented as a transparent overlay widget that paints a polyline using the
    relative coordinates of the two grid cells it is placed over. The pipeline
    view positions the edge across all rows/columns so its geometry is anchored
    to the cards beneath it.
    """

    def __init__(
        self,
        from_pos: tuple[int, int],
        to_pos: tuple[int, int],
        *,
        dashed: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.from_pos = from_pos
        self.to_pos = to_pos
        self.dashed = dashed
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        if self.from_pos[0] >= self.to_pos[0]:
            # Only horizontal-from-left-to-right edges are supported; skip the rest.
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(COLORS["state.danger"] if self.dashed else COLORS["text.muted"])
        pen = QPen(color)
        pen.setWidth(1)
        if self.dashed:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        from_level, _ = self.from_pos
        to_level, _ = self.to_pos
        if to_level - from_level >= 1:
            x1 = (from_level + 1) * 200
            x2 = (to_level) * 200
            mid_y = self.height() / 2
            painter.drawLine(x1, mid_y, x2, mid_y)
            arrow = QPolygonF()
            arrow.append(QPointF(x2, mid_y - 4))
            arrow.append(QPointF(x2 + 6, mid_y))
            arrow.append(QPointF(x2, mid_y + 4))
            painter.drawPolygon(arrow)
        painter.end()


class ProjectRunTimelineView(QWidget):
    """Horizontal time-bar view described in design doc §5 (Timeline view).

    Each attempt becomes its own bar; retries stack as separate bars on the
    same row. Fan-outs read as parallel rows.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_run_id: str | None = None
        self._steps: list[dict[str, Any]] = []
        self._receipt_steps: list[dict[str, Any]] = []
        self._nodes_by_id: dict[str, dict[str, Any]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.canvas = ProjectRunTimelineCanvas()
        layout.addWidget(self.canvas, 1)
        self.summary = QLabel("")
        self.summary.setObjectName("mutedText")
        self.summary.setWordWrap(True)
        apply_type(self.summary, "caption")
        layout.addWidget(self.summary)

        self.empty = EmptyState(
            "Timeline unavailable",
            "Select a Project Run to see its per-step timing.",
            action_text="",
        )
        self.empty.setVisible(False)
        layout.addWidget(self.empty)

    def clear(self) -> None:
        self._project_run_id = None
        self._steps = []
        self._receipt_steps = []
        self._nodes_by_id = {}
        self._render()

    def set_run(
        self,
        project_run_id: str | None,
        steps: list[dict[str, Any]],
        receipt_steps: list[dict[str, Any]] | None,
    ) -> None:
        self._project_run_id = project_run_id
        self._steps = [s for s in steps if isinstance(s, dict)]
        self._receipt_steps = [r for r in (receipt_steps or []) if isinstance(r, dict)]
        self._render()

    def _render(self) -> None:
        if not self._steps:
            self.empty.setVisible(True)
            self.canvas.setVisible(False)
            self.summary.setVisible(False)
            return
        self.empty.setVisible(False)
        self.canvas.setVisible(True)
        self.summary.setVisible(True)

        rows: list[dict[str, Any]] = []
        earliest: datetime | None = None
        latest: datetime | None = None
        receipt_by_node = {str(r.get("node_id") or ""): r for r in self._receipt_steps if isinstance(r, dict)}
        for step in self._steps:
            node_id = str(step.get("node_id") or "")
            status = str(step.get("status") or "").casefold()
            attempts: list[dict[str, Any]] = []
            receipt = receipt_by_node.get(node_id)
            if isinstance(receipt, dict):
                raw = receipt.get("task_runs") or []
                if isinstance(raw, list):
                    attempts = [item for item in raw if isinstance(item, dict)]
            attempts = sorted(attempts, key=lambda item: int(item.get("step_attempt") or 0))
            for attempt in attempts:
                start = _parse_iso(attempt.get("created_at"))
                end = _parse_iso(attempt.get("completed_at"))
                rows.append(
                    {
                        "node_id": node_id,
                        "step_attempt": int(attempt.get("step_attempt") or 0),
                        "status": str(attempt.get("status") or status).casefold(),
                        "worker": str(attempt.get("worker_override") or "").strip(),
                        "started_at": attempt.get("created_at"),
                        "completed_at": attempt.get("completed_at"),
                        "start": start,
                        "end": end,
                    }
                )
                if start and (earliest is None or start < earliest):
                    earliest = start
                if end and (latest is None or end > latest):
                    latest = end
            # Step rows with no receipt attempt still render one bar from step times.
            if not attempts:
                start = _parse_iso(step.get("started_at"))
                end = _parse_iso(step.get("completed_at"))
                rows.append(
                    {
                        "node_id": node_id,
                        "step_attempt": 0,
                        "status": status,
                        "worker": str(step.get("worker_override") or "").strip(),
                        "started_at": step.get("started_at"),
                        "completed_at": step.get("completed_at"),
                        "start": start,
                        "end": end,
                    }
                )
                if start and (earliest is None or start < earliest):
                    earliest = start
                if end and (latest is None or end > latest):
                    latest = end

        rows.sort(key=lambda row: (row["start"] or earliest or datetime.min, row["step_attempt"]))
        self.canvas.set_rows(rows, earliest, latest)
        if not earliest:
            self.summary.setText("No timing information recorded yet.")
            return
        total_seconds = max(0, int((latest - earliest).total_seconds())) if latest else 0
        minutes, seconds = divmod(total_seconds, 60)
        self.summary.setText(
            f"Window: {_format_duration(earliest.isoformat(), latest.isoformat()) if latest else '—'} "
            f"({minutes}m {seconds}s) across {len({row['node_id'] for row in rows})} node(s)."
        )


class ProjectRunTimelineCanvas(QWidget):
    """Draws one bar per attempt; stacked rows keep fan-outs readable."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[dict[str, Any]] = []
        self.earliest: datetime | None = None
        self.latest: datetime | None = None
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: transparent;")

    def set_rows(
        self,
        rows: list[dict[str, Any]],
        earliest: datetime | None,
        latest: datetime | None,
    ) -> None:
        self.rows = rows
        self.earliest = earliest
        self.latest = latest
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.rows or not self.earliest:
            painter.setPen(QColor(COLORS["text.muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "Waiting for timing data.")
            return

        # One row per (node, attempt) pair, deduped by index so retries stack.
        ordered = self.rows
        row_index: dict[tuple[str, int], int] = {}
        for row in ordered:
            key = (row["node_id"], row["step_attempt"])
            row_index.setdefault(key, len(row_index))
        total_rows = max(len(row_index), 1)
        margin_left = 90
        margin_right = 12
        margin_top = 8
        margin_bottom = 28
        chart_width = max(60, self.width() - margin_left - margin_right)
        chart_height = max(40, self.height() - margin_top - margin_bottom)
        row_height = max(8, chart_height // max(total_rows, 1))
        # X scale: seconds from earliest.
        total_seconds = max(1.0, (self.latest - self.earliest).total_seconds()) if self.latest else 1.0

        def x_for(moment: datetime) -> float:
            offset = (moment - self.earliest).total_seconds()
            return margin_left + (offset / total_seconds) * chart_width

        # Axes.
        axis_pen = QPen(QColor(COLORS["text.muted"]))
        axis_pen.setWidth(1)
        painter.setPen(axis_pen)
        painter.drawLine(margin_left, margin_top + chart_height, self.width() - margin_right, margin_top + chart_height)
        if self.latest:
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
                moment = self.earliest + (self.latest - self.earliest) * fraction
                painter.drawLine(
                    margin_left + fraction * chart_width,
                    margin_top + chart_height,
                    margin_left + fraction * chart_width,
                    margin_top + chart_height + 4,
                )
                painter.drawText(
                    int(margin_left + fraction * chart_width - 30),
                    int(margin_top + chart_height + 18),
                    60,
                    14,
                    Qt.AlignCenter,
                    moment.strftime("%H:%M:%S"),
                )

        # Bars.
        for row in ordered:
            row_y = margin_top + row_index[(row["node_id"], row["step_attempt"])] * row_height
            status = row["status"] or "queued"
            color = QColor(_PIPELINE_STATUS_COLORS.get(status, COLORS["text.muted"]))
            label_color = QColor(COLORS["text.primary"])
            muted_color = QColor(COLORS["text.muted"])
            if row["start"]:
                start_x = x_for(row["start"])
            else:
                start_x = margin_left
            if row["end"]:
                end_x = max(start_x + 4, x_for(row["end"]))
            elif row["start"] and self.latest:
                # In-progress: extend to "now".
                end_x = max(start_x + 4, x_for(self.latest))
            else:
                end_x = start_x + 4
            fill = QColor(color)
            fill.setAlpha(160 if status == "blocked" else 220)
            painter.setBrush(fill)
            painter.setPen(Qt.NoPen)
            painter.drawRect(int(start_x), int(row_y + 2), int(end_x - start_x), int(row_height - 4))
            # Node label on the left.
            painter.setPen(QColor(label_color))
            painter.drawText(4, int(row_y + row_height / 2 + 5), f"{row['node_id']} #{row['step_attempt']}")
            # Worker label on the right.
            if row["worker"]:
                painter.setPen(QColor(muted_color))
                painter.drawText(int(end_x + 4), int(row_y + row_height / 2 + 5), row["worker"])
        painter.end()
