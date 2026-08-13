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
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .artifacts import ArtifactExplorerView, ArtifactGroup, ArtifactRecord
from .design_icons import icon
from .design_tokens import COLORS
from .design_typography import apply_type
from .design_widgets import EmptyState, LabeledButton, SegmentedControl, StatusBadge, style_data_table_item
from .scroll_state import preserve_scroll, set_html, set_markdown, set_plain_text

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


def _artifact_uid(artifact: dict[str, Any]) -> str:
    return str(artifact.get("artifact_uid") or "").strip()


def _stable_mapping_signature(value: object) -> str:
    """Create a cheap deterministic signature for polling no-op detection."""

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _artifact_kind(artifact: dict[str, Any]) -> str:
    """Classify an Artifact for a safe read-only preview."""
    mime = str(artifact.get("mime_type") or "").casefold()
    relative_path = str(artifact.get("relative_path") or "").casefold()
    suffix = Path(relative_path).suffix
    if mime == "application/json" or mime.endswith("+json") or suffix == ".json":
        return "json"
    if mime == "text/html" or suffix in {".html", ".htm"}:
        return "html"
    if mime in {"text/markdown", "text/x-markdown"} or suffix in {".md", ".markdown"}:
        return "markdown"
    if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
        return "image"
    if mime == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if mime.startswith("text/") or suffix in {".txt", ".csv", ".log", ".yaml", ".yml", ".xml"}:
        return "text"
    return "unsupported"


def _artifact_merge_key(artifact: dict[str, Any], node_id: str = "") -> str:
    uid = _artifact_uid(artifact)
    if uid:
        return f"uid:{uid}"
    return "path:" + "|".join(
        (
            node_id,
            str(artifact.get("role") or "output"),
            str(artifact.get("relative_path") or ""),
        )
    )


def _merge_project_run_artifacts(
    final_artifacts: list[dict[str, Any]] | None,
    task_artifacts: dict[str, list[dict[str, Any]]] | None,
) -> list[dict[str, Any]]:
    """Merge final-output summaries and lazy per-Task Artifact responses."""
    merged: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}

    def add(item: dict[str, Any], *, node_id: str = "", is_final: bool = False) -> None:
        value = dict(item)
        if node_id and not value.get("node_id"):
            value["node_id"] = node_id
        value["is_final"] = bool(is_final or value.get("is_final"))
        key = _artifact_merge_key(value, str(value.get("node_id") or ""))
        existing_index = indexes.get(key)
        if existing_index is None:
            merged.append(value)
            indexes[key] = len(merged) - 1
            return
        existing = merged[existing_index]
        for field, field_value in value.items():
            if field == "is_final":
                existing[field] = bool(existing.get(field) or field_value)
            elif field_value not in (None, "") and existing.get(field) in (None, ""):
                existing[field] = field_value

    for item in final_artifacts or []:
        if isinstance(item, dict):
            add(item, is_final=True)
    for node_id, items in (task_artifacts or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                add(item, node_id=str(node_id))
    return merged


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
    if str(run.get("workflow_status") or "").casefold() == "needs_review":
        return "Awaiting review · results are ready to inspect"
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
    if status == "awaiting_review":
        blocked = int(run.get("blocked_step_count") or 0)
        suffix = f" · {blocked} step{'s' if blocked != 1 else ''} waiting downstream" if blocked else ""
        return f"Awaiting review{suffix}"
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
    browse_requested = Signal()
    filters_changed = Signal()
    action_requested = Signal(str, str, dict)
    open_output_requested = Signal(str)
    open_run_requested = Signal(str)
    approve_requested = Signal(str, str)
    reject_requested = Signal(str, str)
    open_run_logs_requested = Signal(str)
    open_run_answer_requested = Signal(str)
    open_task_requested = Signal(str)
    reexecute_from_node_requested = Signal(str)
    reexecute_with_comment_requested = Signal(str, str)
    edit_task_requested = Signal(str)
    artifact_preview_requested = Signal(str)
    artifact_path_open_requested = Signal(str)
    artifact_folder_open_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runs: dict[str, dict[str, Any]] = {}
        self.selected_run_id: str | None = None
        self._tree_expanded: dict[str, bool] = {}
        self._group_mode = "date"
        self._browse_panel: QWidget | None = None
        self._runs_signature: str | None = None

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
        grouping = QHBoxLayout()
        grouping.addWidget(QLabel("Group by"))
        self.group_mode = SegmentedControl([("date", "Date"), ("project", "Project")], current="date")
        self.group_mode.value_changed.connect(self._set_group_mode)
        grouping.addWidget(self.group_mode)
        grouping.addStretch(1)
        left.addLayout(grouping)
        self.run_list = QTreeWidget()
        self.run_list.setHeaderLabels(["Project Run", "Status"])
        self.run_list.setRootIsDecorated(True)
        self.run_list.setAlternatingRowColors(True)
        self.run_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        run_header = self.run_list.header()
        run_header.setStretchLastSection(False)
        run_header.setSectionResizeMode(0, QHeaderView.Stretch)
        run_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.run_list.itemClicked.connect(self._on_item_clicked)
        self.run_list.itemExpanded.connect(lambda item: self._remember_tree_state(item, True))
        self.run_list.itemCollapsed.connect(lambda item: self._remember_tree_state(item, False))
        left.addWidget(self.run_list, 1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(380)
        self._browse_panel = left_widget
        body.addWidget(left_widget)

        self.detail = ProjectRunDetailView()
        self.detail.back_to_list_requested.connect(self._show_browser)
        self.detail.action_requested.connect(self.action_requested.emit)
        self.detail.open_output_requested.connect(self.open_output_requested.emit)
        self.detail.open_run_requested.connect(self.open_run_requested.emit)
        self.detail.approve_requested.connect(self.approve_requested.emit)
        self.detail.reject_requested.connect(self.reject_requested.emit)
        self.detail.open_run_logs_requested.connect(self.open_run_logs_requested.emit)
        self.detail.open_run_answer_requested.connect(self.open_run_answer_requested.emit)
        self.detail.open_task_requested.connect(self.open_task_requested.emit)
        self.detail.reexecute_from_node_requested.connect(self.reexecute_from_node_requested.emit)
        self.detail.reexecute_with_comment_requested.connect(self.reexecute_with_comment_requested.emit)
        self.detail.edit_task_requested.connect(self.edit_task_requested.emit)
        self.detail.artifact_preview_requested.connect(self.artifact_preview_requested.emit)
        self.detail.artifact_path_open_requested.connect(self.artifact_path_open_requested.emit)
        self.detail.artifact_folder_open_requested.connect(self.artifact_folder_open_requested.emit)
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
            next_runs = {str(key): dict(value) for key, value in runs.items()}
        else:
            next_runs = {str(run.get("project_run_id")): dict(run) for run in runs if run.get("project_run_id")}
        signature = _stable_mapping_signature(next_runs)
        previous_selected = self.selected_run_id
        self.runs = next_runs
        self._refresh_project_filter()
        self.selected_run_id = selected_run_id
        self._set_browse_mode(self.selected_run_id is None)
        if signature != self._runs_signature or previous_selected != selected_run_id:
            self._runs_signature = signature
            self._render()

    def select_run(self, project_run_id: str | None) -> None:
        self.selected_run_id = str(project_run_id) if project_run_id else None
        self._set_browse_mode(self.selected_run_id is None)
        self._render()

    def set_run_detail(self, project_run_id: str, detail_payload: dict[str, Any]) -> None:
        if str(project_run_id) != self.selected_run_id:
            return
        run_id = str(project_run_id)
        catalog_run = self.runs.setdefault(run_id, {})
        current_detail = self.detail._run if self.detail._run.get("project_run_id") == run_id else {}
        run = dict(current_detail)
        for key, value in catalog_run.items():
            # Catalog refreshes intentionally carry summary data only. Preserve
            # already-loaded detail payloads when a sparse response contains None.
            if value is not None or key not in {"snapshot", "steps", "receipt_steps", "approvals"}:
                run[key] = value
        run.setdefault("project_run_id", run_id)
        if isinstance(detail_payload.get("snapshot"), dict):
            run["snapshot"] = detail_payload["snapshot"]
        if isinstance(detail_payload.get("steps"), list):
            run["steps"] = detail_payload["steps"]
        if isinstance(detail_payload.get("approvals"), list):
            run["approvals"] = detail_payload["approvals"]
        if isinstance(detail_payload.get("reviews"), list):
            run["reviews"] = detail_payload["reviews"]
        self.runs[run_id] = run
        self.detail.set_run(run)

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

    def set_run_reviews(self, project_run_id: str, reviews: list[dict[str, Any]]) -> None:
        run = self.runs.setdefault(str(project_run_id), {})
        run.setdefault("project_run_id", str(project_run_id))
        run["reviews"] = list(reviews)
        self.detail.set_run(self.runs.get(str(project_run_id), {}))

    def clear_selection(self) -> None:
        self.selected_run_id = None
        self._set_browse_mode(True)
        self.detail.set_run({})

    def _set_browse_mode(self, browsing: bool) -> None:
        if self._browse_panel is not None:
            self._browse_panel.setVisible(browsing)
        self.detail.set_browse_mode(browsing)

    def _show_browser(self) -> None:
        self.clear_selection()
        self.browse_requested.emit()

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

    def _set_group_mode(self, mode: str) -> None:
        if mode not in {"date", "project"} or mode == self._group_mode:
            return
        self._group_mode = mode
        self._render()

    def _remember_tree_state(self, item: QTreeWidgetItem, expanded: bool) -> None:
        state_key = item.data(0, Qt.UserRole + 1)
        if state_key:
            self._tree_expanded[str(state_key)] = expanded

    def _render(self) -> None:
        with preserve_scroll(self.run_list):
            self._render_content()

    def _render_content(self) -> None:
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

        if self._group_mode == "project":
            self._render_by_project()
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

    def _render_by_project(self) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        labels: dict[str, str] = {}
        for run in self.runs.values():
            if not self._matches_filters(run):
                continue
            project_id = str(run.get("project_id") or "").strip()
            project_name = str(run.get("project_name") or "").strip()
            key = project_id or f"name:{project_name.casefold()}"
            labels.setdefault(key, project_name or project_id or "Unknown project")
            grouped.setdefault(key, []).append(run)
        for key in sorted(grouped, key=lambda item: labels[item].casefold()):
            rows = grouped[key]
            rows.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)
            state_key = f"project:{key}"
            header = QTreeWidgetItem([f"{labels[key]} · {len(rows)}", ""])
            header.setData(0, Qt.UserRole + 1, state_key)
            header.setFlags(Qt.ItemIsEnabled)
            self.run_list.addTopLevelItem(header)
            date_counts: dict[str, int] = {}
            for run in rows:
                date_label = _local_date(run.get("created_at"))
                date_counts[date_label] = date_counts.get(date_label, 0) + 1
                occurrence = date_counts[date_label]
                row_label = date_label if occurrence == 1 else f"{date_label} ({occurrence})"
                status_label = str(run.get("status") or "Unknown").replace("_", " ").title()
                self._append_run_row(header, run, label=row_label, secondary=status_label)
            header.setExpanded(self._tree_expanded.get(state_key, True))
        self.run_list.setVisible(bool(grouped))

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

    def _append_run_row(
        self,
        parent: QTreeWidgetItem,
        run: dict[str, Any],
        *,
        label: str | None = None,
        secondary: str | None = None,
    ) -> None:
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
        title = label or f"{project_name} · {completed}/{step_count}{suffix}"
        item = QTreeWidgetItem([title, secondary or relative])
        item.setData(0, Qt.UserRole, project_run_id)
        item.setToolTip(
            0,
            "\n".join(part for part in (_verdict(run), str(run.get("created_at") or "")) if part),
        )
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
            "awaiting_review": (COLORS["state.warning"], COLORS["bg.surface"]),
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
    """Right-hand verdict + actions + Workspace/Pipeline/Timeline + inspector."""

    action_requested = Signal(str, str, dict)
    open_output_requested = Signal(str)
    open_run_requested = Signal(str)
    approve_requested = Signal(str, str)
    reject_requested = Signal(str, str)
    open_run_logs_requested = Signal(str)
    open_run_answer_requested = Signal(str)
    open_task_requested = Signal(str)
    reexecute_from_node_requested = Signal(str)
    reexecute_with_comment_requested = Signal(str, str)
    edit_task_requested = Signal(str)
    artifact_preview_requested = Signal(str)
    artifact_path_open_requested = Signal(str)
    artifact_folder_open_requested = Signal(str)
    review_confirm_requested = Signal(str)
    review_rerun_requested = Signal(str, str)
    review_reject_requested = Signal(str, str)
    back_to_list_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run: dict[str, Any] = {}
        self._steps: list[dict[str, Any]] = []
        self._task_run_details: dict[str, dict[str, Any]] = {}
        self._node_artifacts: dict[str, list[dict[str, Any]]] = {}
        self._review_details: dict[str, dict[str, Any]] = {}
        self._run_signature: str | None = None
        self._focused_node_id: str | None = None
        self._pending_artifact_uid: str | None = None
        self._pipeline_inspector_open = False
        self._pipeline_inspector_node_id: str | None = None
        # Nodes the Orchestrator made a repair decision on for this run, kept as
        # a plain instance attribute (not part of self._run) so it survives the
        # polling refreshes that replace self._run wholesale and is only reset
        # when the selected run itself changes, mirroring how receipt caching works.
        self._repaired_node_ids: set[str] = set()

        self.workspace_view = ProjectRunWorkspaceView()
        self.workspace_view.artifact_preview_requested.connect(self.artifact_preview_requested.emit)
        self.workspace_view.open_artifact_requested.connect(self.open_output_requested.emit)
        self.workspace_view.artifact_path_open_requested.connect(self.artifact_path_open_requested.emit)
        self.workspace_view.artifact_folder_open_requested.connect(self.artifact_folder_open_requested.emit)
        self.workspace_view.review_confirm_requested.connect(self.review_confirm_requested.emit)
        self.workspace_view.review_rerun_requested.connect(self.review_rerun_requested.emit)
        self.workspace_view.review_reject_requested.connect(self.review_reject_requested.emit)

        layout = QVBoxLayout(self)

        self.header_row = QHBoxLayout()
        self.back_button = LabeledButton("list", "All Project Runs")
        self.back_button.clicked.connect(self.back_to_list_requested.emit)
        self.back_button.setVisible(False)
        self.header_row.addWidget(self.back_button)
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

        self.review_action_row = QHBoxLayout()
        self.review_action_label = QLabel("")
        self.review_action_label.setObjectName("mutedText")
        self.review_action_label.setWordWrap(True)
        self.review_action_row.addWidget(self.review_action_label, 1)
        self.review_confirm_button = LabeledButton("check-circle", "Confirm review", tone="primary")
        self.review_confirm_button.clicked.connect(self._emit_review_confirm)
        self.review_rerun_button = LabeledButton("repeat", "Rerun with feedback")
        self.review_rerun_button.clicked.connect(self._emit_review_rerun)
        self.review_reject_button = LabeledButton("x-circle", "Reject review", tone="danger")
        self.review_reject_button.clicked.connect(self._emit_review_reject)
        self.review_action_row.addWidget(self.review_confirm_button)
        self.review_action_row.addWidget(self.review_rerun_button)
        self.review_action_row.addWidget(self.review_reject_button)
        layout.addLayout(self.review_action_row)
        self._pending_review_id: str | None = None

        # Kept as a private node-selection model for Inspector compatibility;
        # execution rows are no longer exposed as a public tab.
        self.steps_table = QTableWidget(0, 10, self)
        self.steps_table.setHorizontalHeaderLabels(
            [
                "#",
                "Node",
                "Status",
                "Attempts",
                "Started",
                "Duration",
                "Requested Worker",
                "Actual Worker",
                "Error",
                "Task Run",
            ]
        )
        self.steps_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.steps_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.steps_table.verticalHeader().setVisible(False)
        # Steps remain an internal selection/data model for Pipeline Inspector
        # compatibility. They are not a public tab and must never appear as a
        # default 100x30 child widget in the detail layout.
        self.steps_table.hide()
        header = self.steps_table.horizontalHeader()
        for column in (0, 1, 2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        header.setSectionResizeMode(9, QHeaderView.ResizeToContents)
        self.steps_table.itemSelectionChanged.connect(self._on_step_selection_changed)

        self.pipeline_view = ProjectRunPipelineView()
        self.pipeline_view.node_selected.connect(self._on_pipeline_node_selected)
        self.pipeline_view.artifact_selected.connect(self._on_pipeline_artifact_selected)

        self.artifacts_view = ProjectRunArtifactsView()
        self.artifacts_view.artifact_preview_requested.connect(self.artifact_preview_requested.emit)
        self.artifacts_view.open_artifact_requested.connect(self.open_output_requested.emit)
        self.artifacts_view.artifact_path_open_requested.connect(self.artifact_path_open_requested.emit)
        self.artifacts_view.artifact_folder_open_requested.connect(self.artifact_folder_open_requested.emit)

        self.timeline_view = ProjectRunTimelineView()

        self.orchestrator_view = ProjectRunOrchestratorView()

        self.run_tabs = QTabWidget()
        self.run_tabs.addTab(self.workspace_view, "Workspace")
        self.run_tabs.addTab(self.pipeline_view, "Pipeline")
        self.run_tabs.addTab(self.timeline_view, "Timeline")
        self.run_tabs.addTab(self.orchestrator_view, "Orchestrator")
        self.run_tabs.currentChanged.connect(self._on_run_tab_changed)
        layout.addWidget(self.run_tabs, 1)

        self.inspector = ProjectRunInspectorView()
        self.inspector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.inspector)

        # Forward inspector signals to the outer detail view so MainWindow can wire them.
        self.inspector.open_run_logs_requested.connect(self.open_run_logs_requested.emit)
        self.inspector.open_run_answer_requested.connect(self.open_run_answer_requested.emit)
        self.inspector.open_artifact_requested.connect(self.open_output_requested.emit)
        self.inspector.open_task_requested.connect(self._open_task_from_inspector)
        self.inspector.view_result_requested.connect(self._view_result_from_inspector)
        self.inspector.reexecute_from_node_requested.connect(self.reexecute_from_node_requested.emit)
        self.inspector.reexecute_with_comment_requested.connect(self.reexecute_with_comment_requested.emit)
        self.inspector.edit_task_requested.connect(self.edit_task_requested.emit)
        self.inspector.close_requested.connect(self._close_pipeline_inspector)

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

    def set_browse_mode(self, browsing: bool) -> None:
        self.back_button.setVisible(not browsing and bool(self._run.get("project_run_id")))

    def set_run(self, run: dict[str, Any]) -> None:
        previous_run_id = str(self._run.get("project_run_id") or "")
        signature = _stable_mapping_signature(run if isinstance(run, dict) else {})
        if previous_run_id and previous_run_id == str((run or {}).get("project_run_id") or "") and signature == self._run_signature:
            return
        self._run = dict(run) if isinstance(run, dict) else {}
        self._run_signature = signature
        current_run_id = str(self._run.get("project_run_id") or "")
        if current_run_id != previous_run_id:
            self._pipeline_inspector_open = False
            self._pipeline_inspector_node_id = None
            self._repaired_node_ids = set()
            self._review_details = {}
            self._run_signature = signature
            if current_run_id:
                self.run_tabs.setCurrentWidget(self.workspace_view)
        self._steps = list(self._run.get("steps") or []) if isinstance(self._run.get("steps"), list) else []

        if not self._run.get("project_run_id"):
            self.set_browse_mode(True)
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
            self._set_review_action_state(False)
            self.inspector.clear()
            self.inspector.setVisible(False)
            self._pipeline_inspector_open = False
            self._pipeline_inspector_node_id = None
            self.pipeline_view.clear()
            self.artifacts_view.set_run(None, [], {}, [])
            self.workspace_view.set_run({}, {}, {})
            self.timeline_view.clear()
            self.orchestrator_view.set_orchestrator(None)
            return
        self.empty.setVisible(False)
        self.set_browse_mode(False)
        self.status_badge.setVisible(True)
        self.run_tabs.setVisible(True)
        self.artifact_label.setVisible(True)

        status = str(self._run.get("status") or "unavailable").casefold()
        display_status = "needs_review" if self._run.get("workflow_status") == "needs_review" else status
        self.status_badge.set_status(display_status)
        self.verdict_label.setText(_verdict(self._run))

        is_failed = status == "failed"
        is_terminal = status in {"completed", "failed", "cancelled"}
        self.retry_button.setVisible(is_failed)
        self.reexec_button.setVisible(True)
        self.cancel_button.setVisible(not is_terminal)
        self.output_button.setVisible(status == "completed" and bool(self._run.get("final_artifact_ids")))

        self._render_steps()
        self._render_artifacts()
        self._render_artifacts_tab()
        self._render_workspace()
        self._render_approvals()
        self._render_review_actions()
        self._refresh_inspector_for_current_selection()
        self._render_pipeline()
        self._render_timeline()
        self._restore_pipeline_inspector()
        self._sync_inspector_visibility()

    def _render_steps(self) -> None:
        with preserve_scroll(self.steps_table):
            self._render_steps_content()

    def _render_steps_content(self) -> None:
        steps = self._ordered_steps()
        self.steps_table.setRowCount(len(steps))
        for row, step in enumerate(steps):
            node_id = str(step.get("node_id") or "")
            status = str(step.get("status") or "unavailable").casefold()
            attempt_count = int(step.get("attempt_count") or len(_step_attempts(step)))
            duration = _format_duration(step.get("started_at"), step.get("completed_at"))
            started = str(step.get("started_at") or "")[:19] or (
                "Not started" if status in {"blocked", "pending"} else "—"
            )
            requested_worker = self._requested_worker_for_step(step)
            actual_worker = self._actual_worker_for_step(step)
            error_code = str(step.get("error_code") or "").strip()
            error_text = _humanize_error(error_code) if error_code else "—"
            task_run_id = str(step.get("active_task_run_id") or "")

            values = [
                str(row + 1),
                node_id,
                status.title(),
                str(attempt_count),
                started,
                duration,
                requested_worker,
                actual_worker,
                error_text,
                task_run_id,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 8 and value:
                    item.setData(Qt.UserRole, value)
                    item.setForeground(QColor(COLORS["accent.primary"]))
                elif column in (5, 9):  # Duration, Task Run (id)
                    style_data_table_item(item)
                item.setToolTip(value)
                self.steps_table.setItem(row, column, item)

    def _ordered_steps(self) -> list[dict[str, Any]]:
        snapshot = self._run.get("snapshot")
        definition = snapshot.get("project_definition") if isinstance(snapshot, dict) else None
        nodes = definition.get("nodes") if isinstance(definition, dict) else None
        node_order = {
            str(node.get("node_id")): index
            for index, node in enumerate(nodes or [])
            if isinstance(node, dict) and node.get("node_id")
        }

        def sort_key(step: dict[str, Any]) -> tuple[int, int, float, str]:
            node_id = str(step.get("node_id") or "")
            if node_id in node_order:
                return (0, node_order[node_id], 0.0, node_id)
            started = _parse_iso(step.get("started_at"))
            return (1, 0, started.timestamp() if started else float("inf"), node_id)

        return sorted(self._steps, key=sort_key)

    def _task_run_detail_for_step(self, step: dict[str, Any]) -> dict[str, Any] | None:
        task_run_id = str(step.get("active_task_run_id") or "")
        return self._task_run_details.get(task_run_id) if task_run_id else None

    def _requested_worker_for_step(self, step: dict[str, Any]) -> str:
        detail = self._task_run_detail_for_step(step)
        if isinstance(detail, dict):
            value = str(detail.get("requested_worker") or "").strip()
            if value:
                return value
        value = str(step.get("worker_override") or "").strip()
        if value:
            return value
        task_id = str(step.get("task_id") or "")
        snapshot = self._run.get("snapshot")
        task_snapshots = snapshot.get("task_snapshots") if isinstance(snapshot, dict) else None
        task_snapshot = task_snapshots.get(task_id) if isinstance(task_snapshots, dict) else None
        if isinstance(task_snapshot, dict):
            for key in ("requested_worker", "worker", "worker_override"):
                value = str(task_snapshot.get(key) or "").strip()
                if value:
                    return value
        return "—"

    def _actual_worker_for_step(self, step: dict[str, Any]) -> str:
        detail = self._task_run_detail_for_step(step)
        if isinstance(detail, dict):
            for key in ("actual_worker", "worker"):
                value = str(detail.get(key) or "").strip()
                if value:
                    return value
        attempts = _step_attempts(step)
        if attempts:
            for key in ("worker", "worker_override"):
                value = str(attempts[-1].get(key) or "").strip()
                if value:
                    return value
        return "—"

    def _refresh_step_workers(self) -> None:
        for row in range(self.steps_table.rowCount()):
            node_item = self.steps_table.item(row, 1)
            if not node_item:
                continue
            node_id = node_item.text()
            step = next((item for item in self._steps if str(item.get("node_id") or "") == node_id), {})
            requested_item = self.steps_table.item(row, 6)
            actual_item = self.steps_table.item(row, 7)
            if requested_item is None:
                requested_item = QTableWidgetItem()
                self.steps_table.setItem(row, 6, requested_item)
            if actual_item is None:
                actual_item = QTableWidgetItem()
                self.steps_table.setItem(row, 7, actual_item)
            requested_item.setText(self._requested_worker_for_step(step))
            requested_item.setToolTip(requested_item.text())
            actual_item.setText(self._actual_worker_for_step(step))
            actual_item.setToolTip(actual_item.text())

    def _render_pipeline(self) -> None:
        snapshot = self._run.get("snapshot")
        node_artifacts = {
            node_id: list(items) for node_id, items in self._node_artifacts.items() if isinstance(items, list)
        }
        for final_artifact in self._run.get("final_artifact_ids") or []:
            if not isinstance(final_artifact, dict):
                continue
            node_id = str(final_artifact.get("node_id") or "")
            uid = _artifact_uid(final_artifact)
            if not node_id or not uid:
                continue
            existing_uids = {_artifact_uid(item) for item in node_artifacts.get(node_id, []) if isinstance(item, dict)}
            if uid not in existing_uids:
                node_artifacts.setdefault(node_id, []).append(dict(final_artifact, is_final=True))
        self.pipeline_view.set_run(
            str(self._run.get("project_run_id") or ""),
            snapshot if isinstance(snapshot, dict) else None,
            self._steps,
            self._run.get("receipt_steps") or [],
            node_artifacts,
            repaired_node_ids=self._repaired_node_ids,
        )

    def _render_artifacts_tab(self) -> None:
        review_artifacts: list[dict[str, Any]] = []
        for review_id, review in self._review_details.items():
            if not isinstance(review, dict):
                continue
            review_data = review.get("review") if isinstance(review.get("review"), dict) else review
            if str(review_data.get("status") or "") not in {"pending_human", "needs_human", "delivery_failed"}:
                continue
            for item in review.get("artifacts") or []:
                if isinstance(item, dict):
                    review_artifacts.append(
                        {
                            **item,
                            "review_id": review_id,
                            "node_id": item.get("node_id") or review_data.get("node_id") or "",
                            "publication_status": item.get("publication_status") or "candidate",
                            "is_primary": True,
                        }
                    )
        self.artifacts_view.set_run(
            str(self._run.get("project_run_id") or ""),
            self._run.get("final_artifact_ids") or [],
            self._node_artifacts,
            review_artifacts,
        )

    def _render_timeline(self) -> None:
        self.timeline_view.set_run(
            str(self._run.get("project_run_id") or ""),
            self._steps,
            self._run.get("receipt_steps") or [],
            run_started_at=str(self._run.get("started_at") or "") or None,
            run_created_at=str(self._run.get("created_at") or "") or None,
        )

    def _on_pipeline_node_selected(self, node_id: str) -> None:
        if self._pipeline_inspector_open and self._pipeline_inspector_node_id == node_id:
            self._pipeline_inspector_open = False
            self._pipeline_inspector_node_id = None
            self.steps_table.clearSelection()
            self.inspector.clear()
            self.pipeline_view.select_node(None)
            self._sync_inspector_visibility()
            return
        for row in range(self.steps_table.rowCount()):
            item = self.steps_table.item(row, 1)
            if item and item.text() == node_id:
                self._pipeline_inspector_open = True
                self._pipeline_inspector_node_id = node_id
                self.pipeline_view.select_node(node_id)
                self.steps_table.selectRow(row)
                self._sync_inspector_visibility()
                return

    def _on_pipeline_artifact_selected(self, artifact_uid: str) -> None:
        self.run_tabs.setCurrentWidget(self.workspace_view)
        self.workspace_view.focus_artifact(artifact_uid)

    def _open_task_from_inspector(self, task_id: str) -> None:
        self.open_task_requested.emit(task_id)

    def _view_result_from_inspector(self, node_id: str) -> None:
        self.run_tabs.setCurrentWidget(self.workspace_view)
        self.workspace_view.focus_node_result(node_id)

    def _close_pipeline_inspector(self) -> None:
        self._pipeline_inspector_open = False
        self._pipeline_inspector_node_id = None
        self.steps_table.clearSelection()
        self.inspector.clear()
        self.pipeline_view.select_node(None)
        self._sync_inspector_visibility()

    def _on_run_tab_changed(self, index: int) -> None:
        self._sync_inspector_visibility()
        if index == self.run_tabs.indexOf(self.pipeline_view):
            # Keep pipeline selection synced with the steps table / inspector.
            items = self.steps_table.selectedItems()
            if items:
                row = items[0].row()
                item = self.steps_table.item(row, 1)
                if item:
                    self.pipeline_view.select_node(item.text())

    def _sync_inspector_visibility(self) -> None:
        """Keep the node inspector from consuming Pipeline/Timeline space."""
        has_run = bool(self._run.get("project_run_id"))
        current = self.run_tabs.currentWidget()
        show = has_run and current is self.pipeline_view and self._pipeline_inspector_open
        self.inspector.setVisible(show)

    def _restore_pipeline_inspector(self) -> None:
        if not self._pipeline_inspector_open or not self._pipeline_inspector_node_id:
            return
        target = self._pipeline_inspector_node_id
        for row in range(self.steps_table.rowCount()):
            item = self.steps_table.item(row, 1)
            if item and item.text() == target:
                self.pipeline_view.select_node(target)
                self.steps_table.selectRow(row)
                return
        self._pipeline_inspector_open = False
        self._pipeline_inspector_node_id = None

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
        items = self.steps_table.selectedItems()
        if items:
            row = items[0].row()
            node_item = self.steps_table.item(row, 1)
            node_id = str(node_item.text() if node_item else "")
            if node_id:
                self._pipeline_inspector_open = True
                self._pipeline_inspector_node_id = node_id
                self.pipeline_view.select_node(node_id)
        self._refresh_inspector_for_current_selection()

    def _refresh_inspector_for_current_selection(self) -> None:
        items = self.steps_table.selectedItems()
        if not items:
            self.inspector.clear()
            return
        row = items[0].row()
        node_id_item = self.steps_table.item(row, 1)
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

    def cache_review_detail(self, review_id: str, review: dict[str, Any]) -> None:
        if review_id and isinstance(review, dict):
            self._review_details[str(review_id)] = dict(review)
            self._render_artifacts_tab()
            self._render_workspace()
            self._render_review_actions()

    def _pending_review(self) -> tuple[str, dict[str, Any]] | None:
        for review_id, review in self._review_details.items():
            data = review.get("review") if isinstance(review.get("review"), dict) else review
            if str(data.get("status") or "") in {"pending_human", "needs_human", "delivery_failed"}:
                return review_id, data
        return None

    def _render_review_actions(self) -> None:
        pending = self._pending_review()
        if pending is None:
            self._pending_review_id = None
            self.review_action_label.clear()
            self._set_review_action_state(False)
            return
        review_id, data = pending
        self._pending_review_id = review_id
        round_no = data.get("current_round") or data.get("round_no") or 1
        max_reruns = data.get("max_reruns", 0)
        used = data.get("reruns_used", 0)
        self.review_action_label.setText(
            f"Needs review · round {round_no} · reruns {used}/{max_reruns} · {data.get('reviewer') or 'human'}"
        )
        self.review_action_label.setToolTip(str(data.get("guidelines") or "No additional review guidelines."))
        self._set_review_action_state(True)

    def _set_review_action_state(self, enabled: bool) -> None:
        pending = bool(self._pending_review_id) and enabled
        busy = bool(getattr(self, "_review_action_pending", False))
        self.review_action_label.setVisible(pending)
        self.review_confirm_button.setVisible(pending)
        self.review_rerun_button.setVisible(pending)
        self.review_reject_button.setVisible(pending)
        self.review_confirm_button.setEnabled(pending and not busy)
        self.review_rerun_button.setEnabled(pending and not busy)
        self.review_reject_button.setEnabled(pending and not busy)
        self.workspace_view.set_review_action_pending(busy, self._pending_review_id if pending else None)

    def set_review_action_pending(self, pending: bool, review_id: str | None = None) -> None:
        if review_id and self._pending_review_id and review_id != self._pending_review_id:
            return
        self._review_action_pending = pending
        self._set_review_action_state(bool(self._pending_review_id))

    def review_action_completed(self, review_id: str) -> None:
        if review_id == self._pending_review_id:
            self._pending_review_id = None
        self._review_action_pending = False
        self.workspace_view.review_action_completed(review_id)
        self._set_review_action_state(bool(self._pending_review_id))

    def _emit_review_confirm(self) -> None:
        if self._pending_review_id:
            self.set_review_action_pending(True)
            self.review_confirm_requested.emit(self._pending_review_id)

    def _emit_review_rerun(self) -> None:
        if not self._pending_review_id:
            return
        comment, accepted = QInputDialog.getMultiLineText(
            self, "Rerun with feedback", "What should change in the next attempt?", ""
        )
        if accepted and comment.strip():
            self.set_review_action_pending(True)
            self.review_rerun_requested.emit(self._pending_review_id, comment.strip())

    def _emit_review_reject(self) -> None:
        if not self._pending_review_id:
            return
        reason, accepted = QInputDialog.getMultiLineText(self, "Reject review", "Reason", "")
        if accepted and reason.strip():
            self.set_review_action_pending(True)
            self.review_reject_requested.emit(self._pending_review_id, reason.strip())

    def _render_workspace(self) -> None:
        self.workspace_view.set_run(self._run, self._node_artifacts, self._review_details)

    def cache_orchestrator(self, data: dict[str, Any]) -> None:
        if isinstance(data, dict):
            self.orchestrator_view.set_orchestrator(data)
            events = data.get("events") or []
            self._repaired_node_ids = {
                str(event["node_id"])
                for event in events
                if isinstance(event, dict) and event.get("kind") == "decision" and event.get("node_id")
            }
            self._render_pipeline()

    def cache_orchestrator_error(self, message: str) -> None:
        self.orchestrator_view.set_unavailable(str(message or "Orchestrator data is unavailable."))

    def cache_task_run_detail(self, task_run_id: str, detail: dict[str, Any]) -> None:
        self._task_run_details[str(task_run_id)] = dict(detail) if isinstance(detail, dict) else {}
        self._refresh_step_workers()
        self._refresh_inspector_for_current_selection()

    def cache_task_run_error(self, task_run_id: str, message: str) -> None:
        self._task_run_details[str(task_run_id)] = {
            "status": "UNAVAILABLE",
            "error_message": str(message or "Task Run detail is unavailable."),
        }
        self._refresh_step_workers()
        self._refresh_inspector_for_current_selection()

    def cache_node_artifacts(self, node_id: str, artifacts: list[dict[str, Any]]) -> None:
        self._node_artifacts[str(node_id)] = [item for item in artifacts if isinstance(item, dict)]
        self._render_artifacts_tab()
        self._render_workspace()
        self.pipeline_view.update_node_artifacts(str(node_id), self._node_artifacts[str(node_id)])
        self._refresh_inspector_for_current_selection()

    def cache_node_artifact_error(self, node_id: str, message: str) -> None:
        self._node_artifacts[str(node_id)] = [
            {"role": "unavailable", "relative_path": str(message or "Artifact list is unavailable.")}
        ]
        self._render_artifacts_tab()
        self.pipeline_view.update_node_artifacts(str(node_id), self._node_artifacts[str(node_id)])
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
    open_task_requested = Signal(str)
    view_result_requested = Signal(str)
    reexecute_from_node_requested = Signal(str)
    reexecute_with_comment_requested = Signal(str, str)
    edit_task_requested = Signal(str)
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_run_id: str | None = None
        self._node_id: str | None = None
        self._step: dict[str, Any] = {}
        self._receipt_step: dict[str, Any] = {}
        self._task_run_detail: dict[str, Any] | None = None
        self._artifacts: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)

        header_row = QHBoxLayout()
        self.header_label = QLabel("Select a step to inspect.")
        self.header_label.setObjectName("sectionTitle")
        apply_type(self.header_label, "title.section")
        self.header_label.setWordWrap(True)
        header_row.addWidget(self.header_label, 1)
        self.close_button = QToolButton()
        self.close_button.setText("Close")
        self.close_button.setToolTip("Close node inspector")
        self.close_button.clicked.connect(self.close_requested.emit)
        header_row.addWidget(self.close_button)
        layout.addLayout(header_row)

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
        self.open_task_button = LabeledButton("checklist", "Open Task")
        self.open_task_button.setToolTip("Open this registered Task in the Tasks screen.")
        self.open_task_button.clicked.connect(self._emit_open_task)
        self.view_result_button = LabeledButton("search", "View result", tone="primary")
        self.view_result_button.setToolTip("Open this node's Artifacts and Review in the Project Run Workspace.")
        self.view_result_button.clicked.connect(self._emit_view_result)
        self.reexec_button = LabeledButton("play", "Re-execute from this node")
        self.reexec_button.clicked.connect(self._emit_reexec)
        self.comment_reexec_button = LabeledButton("repeat", "Add comment & re-run")
        self.comment_reexec_button.setToolTip(
            "Append a one-off note to this node's instructions and re-run from here. "
            "The registered Task is not changed; use 'Edit Task' for a bigger change."
        )
        self.comment_reexec_button.clicked.connect(self._emit_reexec_with_comment)
        self.edit_task_button = LabeledButton("pencil", "Edit Task")
        self.edit_task_button.setToolTip("Open this node's Task definition in the Tasks screen.")
        self.edit_task_button.clicked.connect(self._emit_edit_task)
        actions.addWidget(self.open_logs_button)
        actions.addWidget(self.open_answer_button)
        actions.addWidget(self.open_task_button)
        actions.addWidget(self.view_result_button)
        actions.addWidget(self.reexec_button)
        actions.addWidget(self.comment_reexec_button)
        actions.addWidget(self.edit_task_button)
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
        with preserve_scroll(self.attempts_table):
            self._render_attempts_content()

    def _render_attempts_content(self) -> None:
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
            if str(detail.get("status") or "").upper() == "UNAVAILABLE":
                self.requested_worker_label.setText("Unavailable")
                self.actual_worker_label.setText("Unavailable")
                self.task_run_status_label.setText("Unavailable")
                self.task_run_error_label.setText(str(detail.get("error_message") or "Task Run detail is unavailable."))
                return
            requested = str(detail.get("requested_worker") or "").strip() or "—"
            actual = str(detail.get("actual_worker") or "").strip()
            status = str(detail.get("status") or "").strip() or "—"
            error = str(detail.get("error_code") or detail.get("error_message") or "").strip()
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
        with preserve_scroll(self.outputs_table):
            self._render_outputs_content()

    def _render_outputs_content(self) -> None:
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
                elif column == 3:  # Sha256
                    style_data_table_item(item)
                self.outputs_table.setItem(row, column, item)

    def _render_actions(self) -> None:
        active_task_run_id = str(self._step.get("active_task_run_id") or "")
        status = str(self._step.get("status") or "").casefold()
        # The buttons stay visible but disabled when no active Task Run exists.
        self.open_logs_button.setEnabled(bool(active_task_run_id))
        self.open_answer_button.setEnabled(bool(active_task_run_id) and status in {"completed", "partial", "failed"})
        self.open_task_button.setEnabled(bool(self._step.get("task_id")))
        self.view_result_button.setEnabled(bool(self._node_id))
        self.reexec_button.setEnabled(bool(self._project_run_id and self._node_id))
        self.comment_reexec_button.setEnabled(bool(self._project_run_id and self._node_id))
        self.edit_task_button.setEnabled(bool(self._step.get("task_id")))

    def _emit_open_logs(self) -> None:
        active_task_run_id = str(self._step.get("active_task_run_id") or "")
        if active_task_run_id:
            self.open_run_logs_requested.emit(active_task_run_id)

    def _emit_open_answer(self) -> None:
        active_task_run_id = str(self._step.get("active_task_run_id") or "")
        if active_task_run_id:
            self.open_run_answer_requested.emit(active_task_run_id)

    def _emit_open_task(self) -> None:
        task_id = str(self._step.get("task_id") or "")
        if task_id:
            self.open_task_requested.emit(task_id)

    def _emit_view_result(self) -> None:
        if self._node_id:
            self.view_result_requested.emit(self._node_id)

    def _emit_reexec(self) -> None:
        if self._project_run_id and self._node_id:
            self.reexecute_from_node_requested.emit(self._node_id)

    def _emit_reexec_with_comment(self) -> None:
        if not (self._project_run_id and self._node_id):
            return
        comment, accepted = QInputDialog.getMultiLineText(
            self,
            "Add comment & re-run",
            f"Note appended to '{self._node_id}'s instructions for this attempt only "
            "(the registered Task is not changed):",
        )
        if not accepted or not comment.strip():
            return
        self.reexecute_with_comment_requested.emit(self._node_id, comment.strip())

    def _emit_edit_task(self) -> None:
        task_id = str(self._step.get("task_id") or "")
        if task_id:
            self.edit_task_requested.emit(task_id)


class _LegacyProjectRunArtifactsView(QWidget):
    """Task-grouped Artifact catalog with a large read-only preview pane."""

    artifact_preview_requested = Signal(str)
    open_artifact_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_run_id: str | None = None
        self._selected_artifact_uid: str | None = None
        self._artifacts_by_uid: dict[str, dict[str, Any]] = {}
        self._content_by_uid: dict[str, dict[str, Any]] = {}
        self._groups: list[tuple[str, list[dict[str, Any]]]] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.artifact_tree = QTreeWidget()
        self.artifact_tree.setHeaderLabels(["Artifact", "Details"])
        self.artifact_tree.setMinimumWidth(300)
        self.artifact_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.artifact_tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.artifact_tree, 0)

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(12, 0, 0, 0)
        header_row = QHBoxLayout()
        self.preview_header = QLabel("Select an Artifact to preview.")
        self.preview_header.setObjectName("sectionTitle")
        self.preview_header.setWordWrap(True)
        header_row.addWidget(self.preview_header, 1)
        self.open_button = QToolButton()
        self.open_button.setText("Open externally")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._emit_open_selected)
        header_row.addWidget(self.open_button)
        preview_layout.addLayout(header_row)

        self.preview_stack = QStackedWidget()
        self.empty_preview = QLabel("Select an Artifact from the list to preview it.")
        self.empty_preview.setAlignment(Qt.AlignCenter)
        self.empty_preview.setWordWrap(True)
        self.empty_preview.setObjectName("mutedText")
        self.json_preview = QTreeWidget()
        self.json_preview.setHeaderLabels(["Field", "Value"])
        self.json_preview.setColumnWidth(0, 220)
        self.text_preview = QTextBrowser()
        self.text_preview.setObjectName("evidencePane")
        self.text_preview.setOpenExternalLinks(False)
        self.image_preview = QLabel("Image preview unavailable.")
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.setObjectName("evidencePane")
        self.metadata_preview = QLabel("")
        self.metadata_preview.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.metadata_preview.setWordWrap(True)
        self.metadata_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.metadata_preview.setObjectName("mutedText")
        for widget in (
            self.empty_preview,
            self.json_preview,
            self.text_preview,
            self.image_preview,
            self.metadata_preview,
        ):
            self.preview_stack.addWidget(widget)
        preview_layout.addWidget(self.preview_stack, 1)
        layout.addWidget(preview, 1)
        self._show_empty()

    def set_run(
        self,
        project_run_id: str | None,
        final_artifacts: list[dict[str, Any]] | None,
        task_artifacts: dict[str, list[dict[str, Any]]] | None,
    ) -> None:
        project_run_id = str(project_run_id or "") or None
        if project_run_id != self._project_run_id:
            self._selected_artifact_uid = None
            self._content_by_uid = {}
        self._project_run_id = project_run_id
        self._artifacts_by_uid = {}
        final_items = [dict(item, is_final=True) for item in (final_artifacts or []) if isinstance(item, dict)]
        task_groups: list[tuple[str, list[dict[str, Any]]]] = []
        final_uids = {_artifact_uid(item) for item in final_items if _artifact_uid(item)}
        for item in final_items:
            uid = _artifact_uid(item)
            if uid:
                self._artifacts_by_uid[uid] = item
        for node_id, raw_items in (task_artifacts or {}).items():
            items: list[dict[str, Any]] = []
            for raw_item in raw_items if isinstance(raw_items, list) else []:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                item.setdefault("node_id", str(node_id))
                item["is_final"] = bool(item.get("is_final") or _artifact_uid(item) in final_uids)
                uid = _artifact_uid(item)
                if uid:
                    self._artifacts_by_uid.setdefault(uid, item)
                    for key, value in item.items():
                        if value not in (None, "") and self._artifacts_by_uid[uid].get(key) in (None, ""):
                            self._artifacts_by_uid[uid][key] = value
                items.append(item)
            task_groups.append((str(node_id), items))
        self._groups = [("Final Artifacts", final_items), *task_groups]
        self._render_tree()
        if self._selected_artifact_uid:
            self.select_artifact(self._selected_artifact_uid, request_missing=False)
        else:
            self._show_empty()

    def cache_artifact_detail(self, artifact_uid: str, artifact: dict[str, Any]) -> None:
        uid = str(artifact_uid or "")
        if not uid or not isinstance(artifact, dict):
            return
        current = self._artifacts_by_uid.setdefault(uid, {})
        current.update({key: value for key, value in artifact.items() if value not in (None, "")})
        if self._selected_artifact_uid == uid:
            self._render_selected()

    def cache_artifact_content(self, artifact_uid: str, content: dict[str, Any]) -> None:
        uid = str(artifact_uid or "")
        if not uid or not isinstance(content, dict):
            return
        self._content_by_uid[uid] = dict(content)
        if self._selected_artifact_uid == uid:
            self._render_selected()

    def cache_artifact_error(self, artifact_uid: str, message: str) -> None:
        uid = str(artifact_uid or "")
        if not uid:
            return
        self._content_by_uid[uid] = {"available": False, "error": str(message or "Artifact preview is unavailable.")}
        if self._selected_artifact_uid == uid:
            self._render_selected()

    def select_artifact(self, artifact_uid: str, *, request_missing: bool = True) -> None:
        uid = str(artifact_uid or "")
        if not uid:
            self._show_empty()
            return
        self._selected_artifact_uid = uid
        item = self._find_artifact_item(uid)
        if item is not None:
            self.artifact_tree.setCurrentItem(item)
        self._render_selected()
        if request_missing and uid not in self._artifacts_by_uid:
            self.artifact_preview_requested.emit(uid)
        elif request_missing and uid not in self._content_by_uid:
            kind = _artifact_kind(self._artifacts_by_uid.get(uid, {}))
            if kind not in {"image", "unsupported", "pdf"}:
                self.artifact_preview_requested.emit(uid)

    def _render_tree(self) -> None:
        with preserve_scroll(self.artifact_tree):
            self._render_tree_content()

    def _render_tree_content(self) -> None:
        self.artifact_tree.clear()
        for group_name, items in self._groups:
            group = QTreeWidgetItem([group_name, f"{len(items)} item(s)"])
            group.setFlags(Qt.ItemIsEnabled)
            self.artifact_tree.addTopLevelItem(group)
            for item in items:
                role = str(item.get("role") or "output")
                name = str(item.get("relative_path") or item.get("name") or "—")
                detail = str(item.get("mime_type") or _artifact_kind(item)).replace("application/", "")
                child = QTreeWidgetItem([f"{role} · {name}", detail])
                uid = _artifact_uid(item)
                if uid:
                    child.setData(0, Qt.UserRole, uid)
                child.setToolTip(0, name)
                group.addChild(child)
            group.setExpanded(True)

    def _find_artifact_item(self, artifact_uid: str) -> QTreeWidgetItem | None:
        for group_index in range(self.artifact_tree.topLevelItemCount()):
            group = self.artifact_tree.topLevelItem(group_index)
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                if str(child.data(0, Qt.UserRole) or "") == artifact_uid:
                    return child
        return None

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        uid = str(item.data(0, Qt.UserRole) or "")
        if uid:
            self.select_artifact(uid)

    def _emit_open_selected(self) -> None:
        if self._selected_artifact_uid:
            self.open_artifact_requested.emit(self._selected_artifact_uid)

    def _show_empty(self) -> None:
        self._selected_artifact_uid = None
        self.preview_header.setText("Select an Artifact to preview.")
        self.open_button.setEnabled(False)
        self.preview_stack.setCurrentWidget(self.empty_preview)

    def _render_selected(self) -> None:
        uid = self._selected_artifact_uid
        artifact = self._artifacts_by_uid.get(uid or "", {})
        if not uid or not artifact:
            self._show_empty()
            return
        name = str(artifact.get("relative_path") or artifact.get("name") or uid)
        role = str(artifact.get("role") or "output")
        self.preview_header.setText(f"{role} · {name}")
        self.open_button.setEnabled(bool(artifact.get("final_path")))
        kind = _artifact_kind(artifact)
        content = self._content_by_uid.get(uid)
        if isinstance(content, dict) and content.get("error"):
            self.metadata_preview.setText(f"Preview unavailable\n\n{content['error']}")
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind in {"json", "html", "markdown", "text"} and content is None:
            self.metadata_preview.setText("Loading Artifact preview…")
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if (
            kind in {"json", "html", "markdown", "text"}
            and isinstance(content, dict)
            and not content.get("available", True)
        ):
            self.metadata_preview.setText("Artifact content is unavailable.")
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind == "image":
            path = Path(str(artifact.get("final_path") or ""))
            pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
            if not pixmap.isNull():
                self.image_preview.setPixmap(pixmap.scaled(900, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.preview_stack.setCurrentWidget(self.image_preview)
            else:
                self.metadata_preview.setText(f"Image preview unavailable.\n\n{name}")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if kind == "json":
            text = str((content or {}).get("text") or "")
            try:
                value = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                self.metadata_preview.setText("JSON preview unavailable: the content is not valid JSON.")
                self.preview_stack.setCurrentWidget(self.metadata_preview)
                return
            self._populate_json(value)
            self.preview_stack.setCurrentWidget(self.json_preview)
            return
        if kind in {"html", "markdown", "text"} and isinstance(content, dict) and content.get("available"):
            text = str(content.get("text") or "")
            if kind == "html":
                set_html(self.text_preview, text)
            elif kind == "markdown":
                set_markdown(self.text_preview, text)
            else:
                set_plain_text(self.text_preview, text)
            self.preview_stack.setCurrentWidget(self.text_preview)
            return
        if kind in {"pdf", "unsupported"}:
            self.metadata_preview.setText(
                f"In-app preview is not available for this format.\n\n{name}\n"
                f"Size: {artifact.get('size', '—')}\nSHA-256: {artifact.get('sha256', '—')}"
            )
            self.preview_stack.setCurrentWidget(self.metadata_preview)
            return
        if not content:
            self.metadata_preview.setText("Loading Artifact preview…")
        else:
            self.metadata_preview.setText("Artifact content is unavailable.")
        self.preview_stack.setCurrentWidget(self.metadata_preview)

    def _populate_json(self, value: Any) -> None:
        with preserve_scroll(self.json_preview):
            self._populate_json_content(value)

    def _populate_json_content(self, value: Any) -> None:
        self.json_preview.clear()

        def add_value(parent: QTreeWidget | QTreeWidgetItem, key: str, current: Any) -> None:
            if isinstance(current, dict):
                item = QTreeWidgetItem([key, "object"])
                parent.addTopLevelItem(item) if isinstance(parent, QTreeWidget) else parent.addChild(item)
                for child_key, child_value in current.items():
                    add_value(item, str(child_key), child_value)
                item.setExpanded(True)
            elif isinstance(current, list):
                item = QTreeWidgetItem([key, "array"])
                parent.addTopLevelItem(item) if isinstance(parent, QTreeWidget) else parent.addChild(item)
                for index, child_value in enumerate(current):
                    add_value(item, f"[{index}]", child_value)
                item.setExpanded(True)
            else:
                item = QTreeWidgetItem([key, json.dumps(current, ensure_ascii=False)])
                parent.addTopLevelItem(item) if isinstance(parent, QTreeWidget) else parent.addChild(item)

        if isinstance(value, dict):
            for key, child_value in value.items():
                add_value(self.json_preview, str(key), child_value)
        else:
            add_value(self.json_preview, "value", value)


class ProjectRunArtifactsView(ArtifactExplorerView):
    """Project Run adapter over the shared Artifact Explorer.

    The legacy implementation remains below only as a temporary source of
    behavior while the richer renderer task moves its responsibilities into
    ``relay.gui.artifacts``. Existing Project Run callers keep their UID-based
    signals during this transition.
    """

    artifact_preview_requested = Signal(str)
    artifact_path_open_requested = Signal(str)
    artifact_folder_open_requested = Signal(str)
    open_artifact_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preview_requested.connect(lambda uid, _review_id: self.artifact_preview_requested.emit(uid))
        self.open_file_requested.connect(self._handle_open_file)
        self.open_folder_requested.connect(self.artifact_folder_open_requested.emit)

    def set_run(
        self,
        project_run_id: str | None,
        final_artifacts: list[dict[str, Any]] | None,
        task_artifacts: dict[str, list[dict[str, Any]]] | None,
        review_artifacts: list[dict[str, Any]] | None = None,
    ) -> None:
        self.project_run_id = str(project_run_id or "")
        groups: list[ArtifactGroup] = []
        candidate_records = tuple(
            ArtifactRecord.from_mapping(
                item,
                node_id=str(item.get("node_id") or ""),
                review_id=str(item.get("review_id") or ""),
                is_primary=True,
            )
            for item in (review_artifacts or [])
            if isinstance(item, dict)
        )
        if candidate_records:
            label = "Review candidate" + (f" · {candidate_records[0].node_id}" if candidate_records[0].node_id else "")
            groups.append(ArtifactGroup(label, candidate_records))
        final_records = tuple(
            ArtifactRecord.from_mapping(item, is_primary=True)
            for item in (final_artifacts or [])
            if isinstance(item, dict)
        )
        if final_records:
            groups.append(ArtifactGroup("Final Artifacts", final_records))
        for node_id, items in (task_artifacts or {}).items():
            records = tuple(
                ArtifactRecord.from_mapping(
                    item,
                    node_id=str(node_id),
                    is_primary=bool(item.get("is_final")),
                )
                for item in (items if isinstance(items, list) else [])
                if isinstance(item, dict)
            )
            if records:
                groups.append(ArtifactGroup(str(node_id), records))
        merged_groups = [ArtifactGroup(group.label, tuple(ArtifactRecord.merge(group.records))) for group in groups]
        self.set_groups(merged_groups, auto_select_primary=True)

    def cache_artifact_content(self, artifact_uid: str, content: dict[str, Any]) -> None:
        self.cache_content(artifact_uid, content)

    def cache_artifact_error(self, artifact_uid: str, message: str) -> None:
        self.cache_error(artifact_uid, message)

    def focus_node(self, node_id: str) -> bool:
        """Select the first Artifact produced by a Project node."""
        target = str(node_id or "")
        for group in self._groups:
            for record in group.records:
                if target and record.node_id == target and record.artifact_uid:
                    self.select_artifact(record.artifact_uid)
                    return True
        return False

    def _emit_open_uid(self) -> None:
        record = self.selected_record()
        if record and record.artifact_uid:
            self.open_artifact_requested.emit(record.artifact_uid)

    def _handle_open_file(self, path: str) -> None:
        record = self.selected_record()
        if record and record.review_id:
            self.artifact_path_open_requested.emit(path)
        else:
            self._emit_open_uid()


# --- Phase 3 (pipeline view) and Phase 4 (timeline view) helpers --------------


_PIPELINE_STATUS_COLORS: dict[str, str] = {
    "completed": COLORS["state.success"],
    # accent.relay, not the generic state.info blue: a node actively running is
    # the one moment on this screen that's specifically about an agent (or the
    # Orchestrator) doing something right now, and the signature accent is
    # reserved for exactly that (see design_tokens.COLORS["accent.relay"]).
    "running": COLORS["accent.relay"],
    "queued": COLORS["state.warning"],
    "accepted": COLORS["state.warning"],
    "awaiting_approval": COLORS["state.warning"],
    "awaiting_review": COLORS["state.warning"],
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
    artifact_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_run_id: str | None = None
        self._nodes: list[dict[str, Any]] = []
        self._connections: list[dict[str, Any]] = []
        self._steps_by_id: dict[str, dict[str, Any]] = {}
        self._receipt_steps_by_id: dict[str, dict[str, Any]] = {}
        self._node_artifacts_by_id: dict[str, list[dict[str, Any]]] = {}
        self._repaired_node_ids: set[str] = set()
        self._render_signature: str | None = None

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.addWidget(self._build_legend())
        self.cards_container = ProjectRunGraphCanvas()
        self.cards_container_layout = QGridLayout(self.cards_container)
        self.cards_container_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_container_layout.setHorizontalSpacing(72)
        self.cards_container_layout.setVerticalSpacing(24)
        self.pipeline_scroll = QScrollArea()
        self.pipeline_scroll.setObjectName("projectRunPipelineScroll")
        self.pipeline_scroll.setFrameShape(QFrame.NoFrame)
        self.pipeline_scroll.setWidgetResizable(True)
        self.pipeline_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.pipeline_scroll.setWidget(self.cards_container)
        self.body_layout.addWidget(self.pipeline_scroll, 1)
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
        legend_items = (
            ("Completed", "completed"),
            ("Running", "running"),
            ("Failed", "failed"),
            ("Blocked", "blocked"),
            ("Awaiting review", "needs_review"),
            ("Awaiting approval", "needs_approval"),
            ("Cancelled", "cancelled"),
        )
        for label, state in legend_items:
            chip = QLabel(label)
            chip.setObjectName("statusBadge")
            apply_type(chip, "caption")
            chip.setProperty("state", state)
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
        self._node_artifacts_by_id = {}
        self._repaired_node_ids = set()
        self._render_signature = None
        self._render()

    def set_run(
        self,
        project_run_id: str | None,
        snapshot: dict[str, Any] | None,
        steps: list[dict[str, Any]],
        receipt_steps: list[dict[str, Any]] | None = None,
        node_artifacts: dict[str, list[dict[str, Any]]] | None = None,
        *,
        repaired_node_ids: set[str] | None = None,
    ) -> None:
        self._project_run_id = project_run_id
        self._repaired_node_ids = set(repaired_node_ids or ())
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
        self._node_artifacts_by_id = {
            str(node_id): [item for item in items if isinstance(item, dict)]
            for node_id, items in (node_artifacts or {}).items()
            if isinstance(items, list)
        }
        render_signature = _stable_mapping_signature(
            {
                "project_run_id": self._project_run_id,
                "nodes": self._nodes,
                "connections": self._connections,
                "steps": self._steps_by_id,
                "receipt_steps": self._receipt_steps_by_id,
                "node_artifacts": self._node_artifacts_by_id,
                "repaired_node_ids": sorted(self._repaired_node_ids),
            }
        )
        if render_signature == self._render_signature:
            return
        self._render_signature = render_signature
        self._render()

    def update_node_artifacts(self, node_id: str, artifacts: list[dict[str, Any]]) -> None:
        """Update one already-rendered card without rebuilding the whole DAG.

        Node artifact requests arrive independently after the Project Run
        detail. Rebuilding every card for each response made the graph appear
        to flicker and caused repeated layout/paint work. Keep final artifacts
        already attached by the detail renderer and refresh only the affected
        card when the graph is present.
        """

        key = str(node_id or "")
        if not key:
            return
        normalized = [item for item in artifacts if isinstance(item, dict)]
        existing = self._node_artifacts_by_id.get(key, [])
        final_items = [item for item in existing if bool(item.get("is_final"))]
        merged = normalized + [item for item in final_items if _artifact_uid(item) not in {
            _artifact_uid(candidate) for candidate in normalized
        }]
        self._node_artifacts_by_id[key] = merged
        card = next(
            (child for child in self.cards_container.findChildren(ProjectRunNodeCard) if child.node_id == key),
            None,
        )
        if card is None:
            self._render_signature = None
            self._render()
            return
        if card.set_artifacts(merged):
            self.cards_container_layout.activate()
            self.cards_container.updateGeometry()
            self.cards_container.update()

    def select_node(self, node_id: str) -> None:
        """Programmatically highlight a node card (does not emit a signal)."""
        for child in self.cards_container.findChildren(ProjectRunNodeCard):
            child.set_selected(child.node_id == node_id)

    def _render(self) -> None:
        with preserve_scroll(self.pipeline_scroll):
            self._render_content()

    def _render_content(self) -> None:
        # Card construction changes child geometry many times. Keep the
        # viewport frozen until the complete graph, its layout, and its edge
        # specs are ready; otherwise Qt paints each intermediate card as a
        # separate frame, which looks like a small window flickering on screen.
        self.cards_container.setUpdatesEnabled(False)
        self.pipeline_scroll.viewport().setUpdatesEnabled(False)
        try:
            # Clear previous cards and their edges.
            while self.cards_container_layout.count():
                item = self.cards_container_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    # Keep the old card parented until Qt processes deletion.
                    # Detaching a visible QWidget first can briefly promote it
                    # to a top-level native window on Windows.
                    widget.hide()
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
                card = ProjectRunNodeCard(
                    nid,
                    node_def,
                    step,
                    self._receipt_steps_by_id.get(nid),
                    self._node_artifacts_by_id.get(nid, []),
                )
                card.clicked.connect(self._on_card_clicked)
                card.artifact_selected.connect(self.artifact_selected.emit)
                self.cards_container_layout.addWidget(card, row, level)

        # Edges are painted by the graph canvas from the actual card geometries.
        # This keeps arrows out of the layout and prevents zero-length/overlapped
        # lines when the scroll area or card widths change.
            edge_specs: list[dict[str, Any]] = []
            for conn in self._connections:
                from_node = str(conn.get("from_node") or "")
                to_node = str(conn.get("to_node") or "")
                if from_node not in positions or to_node not in positions:
                    continue
                from_step = self._steps_by_id.get(from_node, {})
                to_step = self._steps_by_id.get(to_node, {})
                dashed = (
                    str(from_step.get("status") or "").casefold() == "failed"
                    or str(to_step.get("status") or "").casefold() == "blocked"
                )
                # A quiet, one-color callout for a connection whose source node the
                # Orchestrator actually repaired - only when that node went on to
                # succeed; a still-failed source keeps the dashed/red failure signal,
                # which matters more than "an attempt was made."
                repaired = from_node in self._repaired_node_ids and not dashed
                edge_specs.append(
                    {
                        "from_node": from_node,
                        "to_node": to_node,
                        "dashed": dashed,
                        "repaired": repaired,
                    }
                )
            self.cards_container.set_edge_specs(edge_specs)
            self.cards_container.setMinimumSize(
                max(260, (max_level + 1) * 220),
                max(140, max(len(members) for members in by_level.values()) * 116),
            )
            self.cards_container_layout.activate()
        finally:
            self.pipeline_scroll.viewport().setUpdatesEnabled(True)
            self.cards_container.setUpdatesEnabled(True)
            self.cards_container.updateGeometry()
            self.cards_container.update()

    def _on_card_clicked(self, node_id: str) -> None:
        self.select_node(node_id)
        self.node_selected.emit(node_id)


class ProjectRunGraphCanvas(QWidget):
    """Paint dependency edges behind the real node-card child widgets."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._edge_specs: list[dict[str, Any]] = []
        self.setAttribute(Qt.WA_StyledBackground, True)

    def set_edge_specs(self, specs: list[dict[str, Any]]) -> None:
        self._edge_specs = [spec for spec in specs if isinstance(spec, dict)]
        self.update()

    def edge_segments(self) -> list[dict[str, Any]]:
        """Return actual source/target border points for geometry tests and QA."""
        cards = {card.node_id: card for card in self.findChildren(ProjectRunNodeCard)}
        # Geometry is activated after graph assembly (and on normal Qt layout
        # passes). Avoid forcing a layout pass from every paint event.
        segments: list[dict[str, Any]] = []
        for spec in self._edge_specs:
            source = cards.get(str(spec.get("from_node") or ""))
            target = cards.get(str(spec.get("to_node") or ""))
            if source is None or target is None:
                continue
            source_rect = source.geometry()
            target_rect = target.geometry()
            source_point = QPointF(source_rect.right(), source_rect.center().y())
            target_point = QPointF(target_rect.left(), target_rect.center().y())
            segments.append(
                {
                    "from_node": source.node_id,
                    "to_node": target.node_id,
                    "from": source_point,
                    "to": target_point,
                    "dashed": bool(spec.get("dashed")),
                    "repaired": bool(spec.get("repaired")),
                }
            )
        return segments

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for segment in self.edge_segments():
            if segment["dashed"]:
                color = QColor(COLORS["state.danger"])
            elif segment["repaired"]:
                color = QColor(COLORS["accent.relay"])
            else:
                color = QColor(COLORS["text.muted"])
            pen = QPen(color)
            pen.setWidth(2 if segment["repaired"] else 1)
            if segment["dashed"]:
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            start = segment["from"]
            end = segment["to"]
            painter.drawLine(start, end)
            direction = end - start
            if direction.x() > 1:
                arrow = QPolygonF(
                    [
                        QPointF(end.x() - 7, end.y() - 4),
                        QPointF(end.x(), end.y()),
                        QPointF(end.x() - 7, end.y() + 4),
                    ]
                )
                painter.setBrush(color)
                painter.drawPolygon(arrow)
        painter.end()


class ProjectRunNodeCard(QFrame):
    """Compact node card: icon + node_id + task name + duration/worker + retry badge + error."""

    clicked = Signal(str)
    artifact_selected = Signal(str)

    def __init__(
        self,
        node_id: str,
        node_def: dict[str, Any],
        step: dict[str, Any],
        receipt_step: dict[str, Any] | None,
        node_artifacts: list[dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.node_id = node_id
        self.setObjectName("pipelineNodeCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(150)
        self.setMaximumWidth(220)
        self._artifact_signature: str | None = None

        status = str(step.get("status") or "queued").casefold()
        attempts: list[dict[str, Any]] = []
        if isinstance(receipt_step, dict):
            raw = receipt_step.get("task_runs") or []
            if isinstance(raw, list):
                attempts = [item for item in raw if isinstance(item, dict)]
        attempts = sorted(attempts, key=lambda item: int(item.get("step_attempt") or 0))
        attempt_count = int(step.get("attempt_count") or len(attempts) or 1)
        error_code = str(step.get("error_code") or "").strip()

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
        status_label = QLabel(status.title())
        status_label.setObjectName("pipelineStatusLabel")
        apply_type(status_label, "caption")
        header.addWidget(status_label)
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

        if status == "failed" and error_code:
            err_label = QLabel(_humanize_error(error_code))
            apply_type(err_label, "caption")
            err_label.setWordWrap(True)
            err_label.setObjectName("pipelineErrorCode")
            layout.addWidget(err_label)

        self.artifacts_host = QWidget(self)
        self.artifacts_row = QHBoxLayout(self.artifacts_host)
        self.artifacts_row.setContentsMargins(0, 0, 0, 0)
        self.artifacts_row.setSpacing(4)
        layout.addWidget(self.artifacts_host)
        self._set_artifacts(node_artifacts or [])

        # Visual rules: status tint (color + dashed border) per design doc §5.
        color = _PIPELINE_STATUS_COLORS.get(status, COLORS["text.muted"])
        self.setProperty("pipelineState", status)
        self.setProperty("pipelineColor", color)

    def _set_artifacts(self, artifacts: list[dict[str, Any]]) -> bool:
        normalized = [item for item in artifacts if isinstance(item, dict) and _artifact_uid(item)]
        signature = _stable_mapping_signature(normalized)
        if signature == self._artifact_signature:
            return False
        self._artifact_signature = signature
        self.artifacts_host.setUpdatesEnabled(False)
        try:
            while self.artifacts_row.count():
                item = self.artifacts_row.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            for artifact in normalized:
                uid = _artifact_uid(artifact)
                role = str(artifact.get("role") or "output")
                chip = ProjectRunArtifactChip(uid, role, artifact.get("relative_path"), self.artifacts_host)
                chip.double_clicked.connect(self.artifact_selected.emit)
                self.artifacts_row.addWidget(chip)
            if normalized:
                self.artifacts_row.addStretch(1)
            self.artifacts_host.setVisible(bool(normalized))
        finally:
            self.artifacts_host.setUpdatesEnabled(True)
        return True

    def set_artifacts(self, artifacts: list[dict[str, Any]]) -> bool:
        """Refresh this card's output chips without reconstructing the card."""
        changed = self._set_artifacts(artifacts)
        if changed:
            self.adjustSize()
        return changed

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self.clicked.emit(self.node_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("pipelineSelected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class ProjectRunArtifactChip(QToolButton):
    """Compact, non-invasive Pipeline Artifact target; double-click previews it."""

    double_clicked = Signal(str)

    def __init__(self, artifact_uid: str, role: str, relative_path: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.artifact_uid = artifact_uid
        self.setText(role)
        self.setToolTip(str(relative_path or role))
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(True)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self.double_clicked.emit(self.artifact_uid)
        super().mouseDoubleClickEvent(event)


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
        self._run_started_at: str | None = None
        self._run_created_at: str | None = None

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
        self._run_started_at = None
        self._run_created_at = None
        self._render()

    def set_run(
        self,
        project_run_id: str | None,
        steps: list[dict[str, Any]],
        receipt_steps: list[dict[str, Any]] | None,
        *,
        run_started_at: str | None = None,
        run_created_at: str | None = None,
    ) -> None:
        self._project_run_id = project_run_id
        self._steps = [s for s in steps if isinstance(s, dict)]
        self._receipt_steps = [r for r in (receipt_steps or []) if isinstance(r, dict)]
        self._run_started_at = run_started_at
        self._run_created_at = run_created_at
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
        earliest = _parse_iso(self._run_started_at) or _parse_iso(self._run_created_at)
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
                        "not_started": not start
                        and str(attempt.get("status") or status).casefold()
                        in {"blocked", "pending", "queued", "cancelled"},
                        "display_label": "Not started"
                        if not start
                        and str(attempt.get("status") or status).casefold()
                        in {"blocked", "pending", "queued", "cancelled"}
                        else node_id,
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
                        "not_started": not start and status in {"blocked", "pending", "queued", "cancelled"},
                        "display_label": "Not started"
                        if not start and status in {"blocked", "pending", "queued", "cancelled"}
                        else node_id,
                    }
                )
                if start and (earliest is None or start < earliest):
                    earliest = start
                if end and (latest is None or end > latest):
                    latest = end

        rows.sort(key=lambda row: (row["start"] or earliest or datetime.min, row["step_attempt"]))
        self.canvas.set_rows(rows, earliest, latest)
        if not earliest:
            blocked_count = sum(1 for row in rows if row.get("not_started"))
            self.summary.setText(
                "No timing information recorded yet."
                + (f" {blocked_count} step(s) not started." if blocked_count else "")
            )
            return
        total_seconds = max(0, int((latest - earliest).total_seconds())) if latest else 0
        minutes, seconds = divmod(total_seconds, 60)
        blocked_count = sum(1 for row in rows if row.get("not_started"))
        suffix = f" · {blocked_count} step(s) blocked/not started" if blocked_count else ""
        self.summary.setText(
            f"Window: {_format_duration(earliest.isoformat(), latest.isoformat()) if latest else '—'} "
            f"({minutes}m {seconds}s) across {len({row['node_id'] for row in rows})} node(s){suffix}."
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
            if row.get("not_started"):
                start_x = margin_left
                end_x = start_x + 8
            elif row["start"]:
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
            if row.get("not_started"):
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(fill, 1, Qt.DashLine))
                painter.drawRect(int(start_x), int(row_y + 2), 8, int(max(6, row_height - 4)))
            else:
                painter.setBrush(fill)
                painter.setPen(Qt.NoPen)
                painter.drawRect(int(start_x), int(row_y + 2), int(end_x - start_x), int(row_height - 4))
            # Node label on the left.
            painter.setPen(QColor(label_color))
            label = row.get("display_label") or row["node_id"]
            painter.drawText(4, int(row_y + row_height / 2 + 5), f"{label} #{row['step_attempt']}")
            # Worker label on the right.
            if row["worker"]:
                painter.setPen(QColor(muted_color))
                painter.drawText(int(end_x + 4), int(row_y + row_height / 2 + 5), row["worker"])
        painter.end()


_ORCHESTRATOR_EVENT_ICON: dict[str, str] = {
    "decision": "check-circle",
    "repair": "check-circle",
    # "alert-circle" is not a registered icon name (relay/gui/design_icons.py's
    # ICON_PATHS has no such entry) - any real "report" event crashed this tab
    # with a KeyError before it ever got a chance to render. Never previously
    # exercised by a test because no existing test fed a "report"-kind event
    # through set_orchestrator.
    "report": "file-text",
    "fallback": "alert-triangle",
    "note": "info",
}


def _format_orchestrator_budget(budget: dict[str, Any] | None) -> str:
    if not budget:
        return ""
    repairs_used = budget.get("repair_attempts_used", 0)
    repairs_max = budget.get("max_repair_attempts_per_run")
    calls_used = budget.get("llm_calls_used", 0)
    calls_max = budget.get("max_llm_calls_per_run")
    repairs_max_text = "?" if repairs_max is None else str(repairs_max)
    calls_max_text = "?" if calls_max is None else str(calls_max)
    return f"Repairs {repairs_used}/{repairs_max_text} · Agent calls {calls_used}/{calls_max_text}"


class ProjectRunOrchestratorView(QWidget):
    """Chronological narration/decision stream and budget for one Project Run's Orchestrator.

    Absent or disabled Orchestrator configuration renders an explanatory empty state
    rather than an empty table, so a Project that never attached one reads as "not used
    here" instead of "broken".
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: dict[str, Any] | None = None

        layout = QVBoxLayout(self)

        self.budget_label = QLabel("")
        self.budget_label.setObjectName("mutedText")
        self.budget_label.setVisible(False)
        layout.addWidget(self.budget_label)

        self.event_tree = QTreeWidget()
        self.event_tree.setHeaderLabels(["When", "Actor", "Event"])
        self.event_tree.setColumnWidth(0, 150)
        self.event_tree.setColumnWidth(1, 110)
        self.event_tree.setRootIsDecorated(False)
        self.event_tree.setVisible(False)
        layout.addWidget(self.event_tree, 1)

        self.disabled_state = EmptyState(
            "No Orchestrator attached",
            "Attach an Orchestrator in the Project editor to get automatic narration and "
            "bounded self-repair on this Project's Runs.",
            action_text="",
        )
        layout.addWidget(self.disabled_state)

        self.unavailable_label = QLabel("")
        self.unavailable_label.setObjectName("mutedText")
        self.unavailable_label.setWordWrap(True)
        self.unavailable_label.setVisible(False)
        layout.addWidget(self.unavailable_label)

        self.set_orchestrator(None)

    def set_orchestrator(self, data: dict[str, Any] | None) -> None:
        with preserve_scroll(self.event_tree):
            self._set_orchestrator_content(data)

    def _set_orchestrator_content(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.unavailable_label.setVisible(False)
        enabled = bool(data and data.get("enabled"))
        self.disabled_state.setVisible(not enabled)
        self.budget_label.setVisible(enabled)
        self.event_tree.setVisible(enabled)
        self.event_tree.clear()
        if not enabled:
            return
        self.budget_label.setText(_format_orchestrator_budget(data.get("budget")))
        for event in data.get("events") or []:
            self._add_event_row(event)

    def _add_event_row(self, event: dict[str, Any]) -> None:
        kind = str(event.get("kind") or "note")
        actor = str(event.get("actor") or "")
        summary = str(event.get("summary") or "")
        node_id = event.get("node_id")
        created_at = str(event.get("created_at") or "")[:19]
        prefix = f"[{node_id}] " if node_id else ""
        item = QTreeWidgetItem([created_at, actor, f"{prefix}{summary}"])
        item.setIcon(2, icon(_ORCHESTRATOR_EVENT_ICON.get(kind, "info")))
        item.setToolTip(2, summary)
        self.event_tree.addTopLevelItem(item)

    def set_unavailable(self, message: str) -> None:
        self._data = None
        self.disabled_state.setVisible(False)
        self.event_tree.setVisible(False)
        with preserve_scroll(self.event_tree):
            self.event_tree.clear()
        self.budget_label.setVisible(False)
        self.unavailable_label.setText(message)
        self.unavailable_label.setVisible(True)


class ProjectRunWorkspaceView(QWidget):
    """Action-oriented first surface for a selected Project Run.

    The existing Pipeline, Timeline, and Orchestrator tabs remain the detailed
    evidence views. This surface composes the same artifact data
    with a compact stage rail and an inline review panel so a normal Run can be
    understood and resolved without navigating away from its result.
    """

    artifact_preview_requested = Signal(str)
    open_artifact_requested = Signal(str)
    artifact_path_open_requested = Signal(str)
    artifact_folder_open_requested = Signal(str)
    review_confirm_requested = Signal(str)
    review_rerun_requested = Signal(str, str)
    review_reject_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._run: dict[str, Any] = {}
        self._pending_review_id: str | None = None
        self._review_action_pending = False
        self._run_signature: str | None = None
        self._review_details: dict[str, dict[str, Any]] = {}
        self._pending_update: tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]] | None = None
        self._pending_update_count = 0
        self._rendering = False
        self._user_reading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.update_notice = QFrame()
        self.update_notice.setObjectName("inlineNotice")
        update_layout = QHBoxLayout(self.update_notice)
        self.update_notice_label = QLabel()
        self.update_notice_label.setObjectName("mutedText")
        update_layout.addWidget(self.update_notice_label, 1)
        self.apply_update_button = LabeledButton("refresh", "Apply updates")
        self.apply_update_button.clicked.connect(self._apply_pending_update)
        update_layout.addWidget(self.apply_update_button)
        self.update_notice.setVisible(False)
        root.addWidget(self.update_notice)

        self.next_action = QFrame()
        self.next_action.setObjectName("inlineNotice")
        action_layout = QHBoxLayout(self.next_action)
        self.next_action_label = QLabel()
        self.next_action_label.setWordWrap(True)
        self.next_action_label.setObjectName("projectRunVerdict")
        action_layout.addWidget(self.next_action_label, 1)
        self.review_now_button = LabeledButton("search", "Review now", tone="primary")
        self.review_now_button.clicked.connect(self._focus_review)
        action_layout.addWidget(self.review_now_button)
        root.addWidget(self.next_action)

        self.stage_scroll = QScrollArea()
        self.stage_scroll.setWidgetResizable(True)
        self.stage_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.stage_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stage_host = QWidget()
        self.stage_layout = QHBoxLayout(self.stage_host)
        self.stage_layout.setContentsMargins(2, 4, 2, 8)
        self.stage_scroll.setWidget(self.stage_host)
        root.addWidget(self.stage_scroll)

        self.surface = QFrame()
        self.surface.setObjectName("artifactMetadata")
        surface_layout = QHBoxLayout(self.surface)
        surface_layout.setContentsMargins(8, 8, 8, 8)

        self.artifacts_view = ProjectRunArtifactsView()
        self.artifacts_view.artifact_preview_requested.connect(self.artifact_preview_requested.emit)
        self.artifacts_view.open_artifact_requested.connect(self.open_artifact_requested.emit)
        self.artifacts_view.artifact_path_open_requested.connect(self.artifact_path_open_requested.emit)
        self.artifacts_view.artifact_folder_open_requested.connect(self.artifact_folder_open_requested.emit)
        self.artifacts_view.artifact_tree.itemSelectionChanged.connect(self._mark_artifact_reading)
        surface_layout.addWidget(self.artifacts_view, 3)

        self.review_panel = QFrame()
        self.review_panel.setObjectName("inlineNotice")
        review_layout = QVBoxLayout(self.review_panel)
        self.review_title = QLabel("Review")
        self.review_title.setObjectName("sectionTitle")
        review_layout.addWidget(self.review_title)
        self.review_meta = QLabel()
        self.review_meta.setObjectName("mutedText")
        self.review_meta.setWordWrap(True)
        review_layout.addWidget(self.review_meta)
        self.review_guidelines = QLabel()
        self.review_guidelines.setWordWrap(True)
        self.review_guidelines.setObjectName("mutedText")
        review_layout.addWidget(self.review_guidelines)
        self.review_checklist = QLabel()
        self.review_checklist.setWordWrap(True)
        review_layout.addWidget(self.review_checklist)
        self.review_evaluation = QLabel()
        self.review_evaluation.setWordWrap(True)
        self.review_evaluation.setObjectName("mutedText")
        review_layout.addWidget(self.review_evaluation)
        self.review_handoff = QLabel()
        self.review_handoff.setWordWrap(True)
        self.review_handoff.setObjectName("mutedText")
        review_layout.addWidget(self.review_handoff)
        self.review_rounds = QTreeWidget()
        self.review_rounds.setHeaderLabels(["Round", "Status", "Evidence / decision"])
        self.review_rounds.setRootIsDecorated(False)
        self.review_rounds.setMaximumHeight(132)
        self.review_rounds.setVisible(False)
        review_layout.addWidget(self.review_rounds)
        self.review_comment = QTextEdit()
        self.review_comment.setPlaceholderText("What should change before the next attempt?")
        self.review_comment.setMaximumHeight(84)
        self.review_comment.setObjectName("reviewComment")
        self.review_comment.textChanged.connect(lambda: self._set_review_controls(bool(self._pending_review_id)))
        self.review_comment.textChanged.connect(self._mark_user_reading)
        review_layout.addWidget(self.review_comment)
        review_actions = QHBoxLayout()
        self.review_confirm = LabeledButton("check-circle", "Confirm", tone="primary")
        self.review_rerun = LabeledButton("repeat", "Rerun")
        self.review_reject = LabeledButton("x-circle", "Reject", tone="danger")
        self.review_confirm.clicked.connect(self._emit_confirm)
        self.review_rerun.clicked.connect(self._emit_rerun)
        self.review_reject.clicked.connect(self._emit_reject)
        review_actions.addWidget(self.review_confirm)
        review_actions.addWidget(self.review_rerun)
        review_actions.addWidget(self.review_reject)
        review_layout.addLayout(review_actions)
        review_layout.addStretch(1)
        surface_layout.addWidget(self.review_panel, 1)
        root.addWidget(self.surface, 1)

        self.set_run({}, {}, {})

    def set_run(
        self,
        run: dict[str, Any],
        node_artifacts: dict[str, list[dict[str, Any]]],
        review_details: dict[str, dict[str, Any]],
    ) -> None:
        next_run = dict(run) if isinstance(run, dict) else {}
        run_id = str(next_run.get("project_run_id") or "")
        signature = _stable_mapping_signature(
            {
                "run": next_run,
                "artifacts": node_artifacts,
                "reviews": review_details,
            }
        )
        if (
            run_id
            and str(self._run.get("project_run_id") or "") == run_id
            and self._run_signature
            and signature != self._run_signature
            and self._user_reading
        ):
            self._pending_update = (next_run, dict(node_artifacts), dict(review_details))
            self._pending_update_count += 1
            self.update_notice_label.setText(
                f"{self._pending_update_count} new update{'s' if self._pending_update_count != 1 else ''} available"
            )
            self.update_notice.setVisible(True)
            return
        self._apply_run(next_run, node_artifacts, review_details, signature)

    def _apply_run(
        self,
        run: dict[str, Any],
        node_artifacts: dict[str, list[dict[str, Any]]],
        review_details: dict[str, dict[str, Any]],
        signature: str | None = None,
    ) -> None:
        previous_run_id = str(self._run.get("project_run_id") or "")
        next_run_id = str(run.get("project_run_id") or "")
        if previous_run_id != next_run_id:
            self._user_reading = False
            self._focused_node_id = None
            self._pending_artifact_uid = None
        self._rendering = True
        self._run = dict(run)
        self._review_details = dict(review_details)
        self._run_signature = signature or _stable_mapping_signature(
            {"run": run, "artifacts": node_artifacts, "reviews": review_details}
        )
        self._pending_update = None
        self._pending_update_count = 0
        self.update_notice.setVisible(False)
        self._render_run(node_artifacts, review_details)
        self._rendering = False

    def _render_run(
        self,
        node_artifacts: dict[str, list[dict[str, Any]]],
        review_details: dict[str, dict[str, Any]],
    ) -> None:
        run_id = str(self._run.get("project_run_id") or "")
        if not run_id:
            self.next_action_label.setText("Select a Project Run to see its next action and results.")
            self.review_now_button.setVisible(False)
            self.review_panel.setVisible(False)
            self._clear_stages()
            self.artifacts_view.set_run(None, [], {}, [])
            return

        self.review_now_button.setVisible(False)
        self.review_panel.setVisible(True)
        self._render_stages()
        review_artifacts = self._review_artifacts(review_details)
        self.artifacts_view.set_run(
            run_id,
            self._run.get("final_artifact_ids") or [],
            node_artifacts,
            review_artifacts,
        )
        if self._pending_artifact_uid:
            pending_uid = self._pending_artifact_uid
            self.artifacts_view.select_artifact(pending_uid)
            if self.artifacts_view.selected_record() is not None:
                self._pending_artifact_uid = None
                self._focused_node_id = self.artifacts_view.selected_record().node_id or self._focused_node_id
        elif self._focused_node_id:
            self.artifacts_view.focus_node(self._focused_node_id)
        review = self._workspace_review(review_details, self._focused_node_id)
        self._render_next_action(review)
        self._render_review(review)

    def focus_artifact(self, artifact_uid: str) -> None:
        """Focus one Project Run Artifact in the canonical Workspace."""
        uid = str(artifact_uid or "")
        if not uid:
            return
        self._pending_artifact_uid = uid
        self.artifacts_view.select_artifact(uid)
        record = self.artifacts_view.selected_record()
        if record is not None:
            self._pending_artifact_uid = None
            self._focused_node_id = record.node_id or self._focused_node_id
            self._render_review(self._workspace_review(self._review_details, self._focused_node_id))

    def focus_node_result(self, node_id: str) -> None:
        """Focus a node's Artifact and matching Review without leaving the Run."""
        target = str(node_id or "")
        if not target:
            return
        self._focused_node_id = target
        self.artifacts_view.focus_node(target)
        self._render_review(self._workspace_review(self._review_details, target))

    def _apply_pending_update(self) -> None:
        pending = self._pending_update
        if pending is None:
            return
        run, artifacts, reviews = pending
        self._apply_run(
            run,
            artifacts,
            reviews,
            _stable_mapping_signature({"run": run, "artifacts": artifacts, "reviews": reviews}),
        )

    def _mark_user_reading(self) -> None:
        if not self._rendering and self.review_comment.toPlainText().strip():
            self._user_reading = True

    def _mark_artifact_reading(self, *_args) -> None:
        if not self._rendering:
            self._user_reading = True

    def acknowledge_updates(self) -> None:
        self._apply_pending_update()

    def _render_next_action(self, pending: tuple[str, dict[str, Any], dict[str, Any]] | None) -> None:
        status = str(self._run.get("status") or "").casefold()
        workflow = str(self._run.get("workflow_status") or "").casefold()
        review_status = str(pending[1].get("status") or "").casefold() if pending else ""
        if review_status == "evaluating":
            self.next_action_label.setText("Orchestrator is evaluating the candidate — the result will appear here when ready.")
            self.review_now_button.setVisible(False)
        elif workflow == "needs_review" or review_status in {"pending_human", "needs_human", "delivery_failed"}:
            self.next_action_label.setText(
                "검수 필요 — 결과물을 확인한 뒤 Confirm 하거나 코멘트를 남기고 Rerun 하세요."
            )
            self.review_now_button.setVisible(True)
        elif status == "failed":
            self.next_action_label.setText("실패한 단계가 있습니다 — Pipeline에서 원인을 확인하고 Retry 하세요.")
        elif status in {"running", "queued", "pending"}:
            self.next_action_label.setText("실행 중 — 현재 단계와 최근 활동은 Pipeline과 Timeline에서 확인할 수 있습니다.")
        elif status == "completed":
            self.next_action_label.setText("실행 완료 — 최종 Artifact를 확인하거나 Open output으로 결과 폴더를 여세요.")
        else:
            self.next_action_label.setText("이 Run의 상태와 결과물을 확인하세요.")

    def _clear_stages(self) -> None:
        while self.stage_layout.count():
            item = self.stage_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _render_stages(self) -> None:
        self._clear_stages()
        steps = self._run.get("steps") or []
        if not isinstance(steps, list) or not steps:
            label = QLabel("No step status available yet")
            label.setObjectName("mutedText")
            self.stage_layout.addWidget(label)
            self.stage_layout.addStretch(1)
            return
        for step in steps:
            if not isinstance(step, dict):
                continue
            node = str(step.get("node_id") or step.get("task_id") or "Step")
            status = str(step.get("status") or "pending").casefold()
            frame = QFrame()
            frame.setObjectName("inlineNotice")
            frame.setMinimumWidth(125)
            layout = QVBoxLayout(frame)
            title = QLabel(node)
            title.setObjectName("sectionTitle")
            title.setWordWrap(True)
            state = QLabel(_workspace_status_text(status))
            state.setObjectName("mutedText")
            layout.addWidget(title)
            layout.addWidget(state)
            self.stage_layout.addWidget(frame)
        self.stage_layout.addStretch(1)

    def _workspace_review(
        self, review_details: dict[str, dict[str, Any]], preferred_node_id: str | None = None
    ) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
        actionable = {"pending_human", "needs_human", "delivery_failed", "evaluating"}
        preferred = str(preferred_node_id or "")
        candidates = list(review_details.items())
        if preferred:
            def review_matches_node(entry: tuple[str, dict[str, Any]]) -> bool:
                review = entry[1]
                data = review.get("review") if isinstance(review.get("review"), dict) else review
                if str(data.get("node_id") or "") == preferred:
                    return True
                return any(
                    isinstance(artifact, dict) and str(artifact.get("node_id") or "") == preferred
                    for artifact in review.get("artifacts") or []
                )

            candidates = sorted(
                candidates,
                key=lambda entry: 0 if review_matches_node(entry) else 1,
            )
        for review_id, review in candidates:
            data = review.get("review") if isinstance(review.get("review"), dict) else review
            if str(data.get("status") or "") in actionable:
                return str(review_id), data, review
        for review_id, review in candidates:
            data = review.get("review") if isinstance(review.get("review"), dict) else review
            return str(review_id), data, review
        return None

    def _review_artifacts(self, review_details: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for review_id, review in review_details.items():
            data = review.get("review") if isinstance(review.get("review"), dict) else review
            if str(data.get("status") or "") not in {
                "pending_human",
                "needs_human",
                "delivery_failed",
                "evaluating",
            }:
                continue
            for artifact in review.get("artifacts") or []:
                if isinstance(artifact, dict):
                    records.append({**artifact, "review_id": review_id, "is_primary": True})
        return records

    def _render_review(self, pending: tuple[str, dict[str, Any], dict[str, Any]] | None) -> None:
        self._pending_review_id = pending[0] if pending else None
        if not pending:
            self.review_title.setText("Review")
            self.review_meta.setText("No review gate is waiting for action.")
            self.review_guidelines.clear()
            self.review_checklist.setText("✓ Execution status and delivered Artifacts remain available in Workspace.")
            self.review_evaluation.clear()
            self.review_handoff.clear()
            self.review_rounds.clear()
            self.review_rounds.setVisible(False)
            self._set_review_controls(False)
            return
        review_id, data, detail = pending
        round_no = data.get("current_round") or data.get("round_no") or 1
        used = data.get("reruns_used", 0)
        maximum = data.get("max_reruns", 0)
        status = str(data.get("status") or "").casefold()
        self.review_title.setText("Review needed")
        self.review_meta.setText(
            f"Reviewer: {data.get('reviewer') or 'human'} · Status: {_review_status_text(status)} · "
            f"Round {round_no} · Reruns {used}/{maximum}\n{review_id[:16]}"
        )
        self.review_guidelines.setText(
            f"<b>Guidelines</b><br>{_escape_review_text(data.get('guidelines') or 'Inspect the candidate result and decide whether it is ready.') }"
        )
        self.review_checklist.setText(
            "✓ Upstream steps available\n✓ Candidate Artifact selected\n"
            + ("○ Content quality requires your decision" if status != "evaluating" else "○ Orchestrator evaluation in progress")
        )
        evaluation = _decode_review_evaluation(data.get("evaluation_json"))
        if evaluation:
            decision = _review_decision_text(evaluation.get("decision"))
            reason = str(evaluation.get("reason") or evaluation.get("comment") or "No reason recorded.")
            self.review_evaluation.setText(f"<b>Orchestrator evaluation:</b> {decision}<br>{_escape_review_text(reason)}")
        elif status == "evaluating":
            self.review_evaluation.setText("<b>Orchestrator evaluation:</b> In progress…")
        else:
            self.review_evaluation.clear()
        handoff_reason = str(data.get("comment") or "")
        if status in {"needs_human", "delivery_failed"} and handoff_reason:
            self.review_handoff.setText(f"<b>Human handoff:</b> {_escape_review_text(handoff_reason)}")
        else:
            self.review_handoff.clear()
        self._render_review_rounds(detail.get("rounds") or [])
        self._set_review_controls(status in {"pending_human", "needs_human", "delivery_failed"})

    def _render_review_rounds(self, rounds: list[dict[str, Any]]) -> None:
        self.review_rounds.clear()
        for round_data in rounds:
            if not isinstance(round_data, dict):
                continue
            evaluation = _decode_review_evaluation(round_data.get("evaluation_json"))
            decision = _review_decision_text(evaluation.get("decision")) if evaluation else ""
            detail = decision or str(round_data.get("comment") or round_data.get("task_run_id") or "—")
            item = QTreeWidgetItem(
                [
                    str(round_data.get("round_no") or "—"),
                    _review_status_text(str(round_data.get("status") or "")),
                    detail,
                ]
            )
            item.setToolTip(2, str(round_data.get("task_run_id") or ""))
            self.review_rounds.addTopLevelItem(item)
        self.review_rounds.setVisible(self.review_rounds.topLevelItemCount() > 0)

    def _set_review_controls(self, enabled: bool) -> None:
        busy = self._review_action_pending
        has_comment = bool(self.review_comment.toPlainText().strip())
        self.review_confirm.setEnabled(enabled and not busy)
        self.review_rerun.setEnabled(enabled and has_comment and not busy)
        self.review_reject.setEnabled(enabled and has_comment and not busy)

    def set_review_action_pending(self, pending: bool, review_id: str | None = None) -> None:
        if review_id and self._pending_review_id and review_id != self._pending_review_id:
            return
        self._review_action_pending = pending
        self._set_review_controls(bool(self._pending_review_id))

    def review_action_completed(self, review_id: str) -> None:
        if review_id == self._pending_review_id:
            self._pending_review_id = None
            self.review_comment.blockSignals(True)
            self.review_comment.clear()
            self.review_comment.blockSignals(False)
        self._review_action_pending = False
        self._set_review_controls(bool(self._pending_review_id))

    def _focus_review(self) -> None:
        self.review_comment.setFocus()

    def _emit_confirm(self) -> None:
        if self._pending_review_id and not self._review_action_pending:
            self.set_review_action_pending(True)
            self.review_confirm_requested.emit(self._pending_review_id)

    def _emit_rerun(self) -> None:
        comment = self.review_comment.toPlainText().strip()
        if self._pending_review_id and comment and not self._review_action_pending:
            self.set_review_action_pending(True)
            self.review_rerun_requested.emit(self._pending_review_id, comment)

    def _emit_reject(self) -> None:
        comment = self.review_comment.toPlainText().strip()
        if self._pending_review_id and comment and not self._review_action_pending:
            self.set_review_action_pending(True)
            self.review_reject_requested.emit(self._pending_review_id, comment)


def _workspace_status_text(status: str) -> str:
    return {
        "completed": "Complete",
        "succeeded": "Complete",
        "failed": "Failed",
        "running": "Running",
        "queued": "Queued",
        "pending": "Not started",
        "blocked": "Blocked",
        "awaiting_review": "Needs review",
    }.get(status, status.replace("_", " ").title() or "Unknown")


def _escape_review_text(value: object) -> str:
    from html import escape

    return escape(str(value)).replace("\n", "<br>")


def _decode_review_evaluation(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _review_decision_text(value: object) -> str:
    return {
        "approve": "Confirmed",
        "rerun": "Rerun requested",
        "human_review": "Human review required",
    }.get(str(value or "").casefold(), str(value or "Evaluation recorded"))


def _review_status_text(value: str) -> str:
    return {
        "pending_human": "Needs review",
        "needs_human": "Human handoff",
        "delivery_failed": "Delivery needs attention",
        "evaluating": "Evaluating",
        "approved": "Confirmed",
        "rerun_requested": "Rerun requested",
        "rejected": "Rejected",
        "pending": "Pending",
    }.get(value.casefold(), value.replace("_", " ").title() or "Unknown")
