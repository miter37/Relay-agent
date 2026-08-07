"""Task Run master/detail UI.

Runs are execution history, not Task definitions.  This view owns the Run
list, its filters, and the selected Run detail so every catalog section has a
consistent list-plus-detail shape.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .design_tokens import COLORS
from .job_detail import TaskRunDetailView


class RunsView(QWidget):
    """Browse Task Runs and render the selected Run beside the list."""

    select_run_requested = Signal(str)
    filters_changed = Signal()
    load_more_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.jobs: dict[str, dict] = {}
        self.selected_run_id: str | None = None
        self._tree_expanded: dict[str, bool] = {}

        # No section heading here: the top bar names the section.
        root = QVBoxLayout(self)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search Task Runs, Tasks, Agents…")
        self.search_edit.textChanged.connect(lambda _text: self._on_filters_changed())
        filters.addWidget(self.search_edit, 2)
        self.result_filter = self._combo("Result", ["All", "Completed", "Partial", "Failed", "Cancelled"])
        self.agent_filter = self._combo("Agent", ["All", "Claude", "Codex", "Antigravity"])
        self.source_filter = self._combo("Source", ["All", "Command line", "GUI", "Hermes", "Schedule"])
        self.date_filter = self._combo("Date", ["Any time", "Today", "Last 7 days", "Last 30 days"])
        for control in (self.result_filter, self.agent_filter, self.source_filter, self.date_filter):
            control.currentIndexChanged.connect(lambda _index: self._on_filters_changed())
            filters.addWidget(control)
        root.addLayout(filters)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.run_list = QTreeWidget()
        self.run_list.setHeaderLabels(["Task Run", "Status"])
        self.run_list.setColumnWidth(0, 260)
        self.run_list.setRootIsDecorated(True)
        self.run_list.setAlternatingRowColors(True)
        self.run_list.itemClicked.connect(self._on_item_clicked)
        self.run_list.itemExpanded.connect(lambda item: self._remember_tree_state(item, True))
        self.run_list.itemCollapsed.connect(lambda item: self._remember_tree_state(item, False))
        left.addWidget(self.run_list, 1)
        self.load_more_button = QPushButton("Load more")
        self.load_more_button.clicked.connect(self.load_more_requested.emit)
        self.load_more_button.setEnabled(False)
        left.addWidget(self.load_more_button)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(380)
        body.addWidget(left_widget)

        self.detail = TaskRunDetailView()
        body.addWidget(self.detail, 1)
        root.addLayout(body, 1)

    @staticmethod
    def _combo(name: str, values: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(f"runs_{name.lower().replace(' ', '_')}")
        combo.addItems(values)
        return combo

    def filters(self) -> dict[str, str]:
        return {
            "search": self.search_edit.text().strip(),
            "result": self.result_filter.currentText(),
            "agent": self.agent_filter.currentText(),
            "source": self.source_filter.currentText(),
            "date": self.date_filter.currentText(),
        }

    def set_runs(self, jobs: dict[str, dict], *, selected_run_id: str | None, has_more: bool = False) -> None:
        self.jobs = dict(jobs)
        self.selected_run_id = selected_run_id
        self.load_more_button.setEnabled(has_more)
        self._render()

    def select_run(self, job_id: str) -> None:
        self.selected_run_id = job_id
        self._render()

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        job_id = item.data(0, Qt.UserRole)
        if job_id:
            self.select_run_requested.emit(str(job_id))

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

        groups = (
            ("Waiting", {"CREATED", "QUEUED"}, "created_at"),
            ("Running", {"PREPARING", "RUNNING", "VALIDATING", "DELIVERING", "CANCEL_REQUESTED"}, "started_at"),
            ("Finished", {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}, "completed_at"),
        )
        for group_name, statuses, date_key in groups:
            rows = [job for job in self.jobs.values() if job.get("status") in statuses and self._matches_filters(job)]
            rows.sort(key=lambda job: job.get(date_key) or job.get("created_at") or "", reverse=True)
            if not rows:
                continue
            group_key = f"group:{group_name}"
            header = QTreeWidgetItem([f"{group_name} · {len(rows)}", ""])
            header.setData(0, Qt.UserRole + 1, group_key)
            header.setFlags(Qt.ItemIsEnabled)
            self.run_list.addTopLevelItem(header)
            date_groups = {"All": rows}
            if group_name == "Finished":
                date_groups = {}
                for job in rows:
                    date_groups.setdefault(self._local_date(job.get(date_key) or job.get("created_at")), []).append(job)
            for date_name, date_rows in date_groups.items():
                parent = header
                if group_name == "Finished":
                    date_group_key = f"date:{group_name}:{date_name}"
                    parent = QTreeWidgetItem([f"{date_name} · {len(date_rows)}", ""])
                    parent.setData(0, Qt.UserRole + 1, date_group_key)
                    parent.setFlags(Qt.ItemIsEnabled)
                    header.addChild(parent)
                for job in date_rows:
                    job_id = str(job.get("job_id") or job.get("task_run_id") or "")
                    title = job.get("title") or job_id[:8] or "Task Run"
                    status = str(job.get("status") or "UNKNOWN")
                    item = QTreeWidgetItem([str(title), self._status_text(status)])
                    item.setData(0, Qt.UserRole, job_id)
                    item.setToolTip(0, str(job.get("task_preview") or job_id))
                    item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    self._apply_status_colors(item, status)
                    parent.addChild(item)
                    if job_id == self.selected_run_id:
                        self.run_list.setCurrentItem(item)
                if group_name == "Finished":
                    parent.setExpanded(self._tree_expanded.get(date_group_key, True))
            header.setExpanded(self._tree_expanded.get(group_key, True))

    def _matches_filters(self, job: dict) -> bool:
        filters = self.filters()
        query = filters["search"].casefold()
        haystack = " ".join(
            str(job.get(key) or "") for key in ("title", "task_preview", "job_id", "requested_worker", "actual_worker")
        )
        if query and query not in haystack.casefold():
            return False
        if filters["result"] != "All" and str(job.get("status") or "").casefold() != filters["result"].casefold():
            return False
        if filters["agent"] != "All" and filters["agent"].casefold() not in {
            str(job.get("requested_worker") or "").casefold(),
            str(job.get("actual_worker") or "").casefold(),
        }:
            return False
        source = {"Command line": "cli", "GUI": "gui", "Hermes": "hermes", "Schedule": "schedule"}.get(
            filters["source"], filters["source"].casefold()
        )
        return filters["source"] == "All" or str(job.get("submitted_via") or "").casefold() == source

    @staticmethod
    def _status_text(status: str) -> str:
        return {
            "COMPLETED": "Okay",
            "PARTIAL": "Partial",
            "FAILED": "Fail",
            "CANCELLED": "Cancelled",
            "QUEUED": "Queued",
        }.get(status, status.title())

    @staticmethod
    def _apply_status_colors(item: QTreeWidgetItem, status: str) -> None:
        colors = {
            "COMPLETED": (COLORS["state.success"], COLORS["bg.surface"]),
            "PARTIAL": (COLORS["state.warning"], COLORS["bg.surface"]),
            "FAILED": (COLORS["state.danger"], COLORS["bg.surface"]),
            "CANCELLED": (COLORS["text.muted"], COLORS["bg.surface"]),
        }
        if status not in colors:
            return
        foreground, background = colors[status]
        for column in range(2):
            item.setForeground(column, QColor(foreground))
            item.setBackground(column, QColor(background))

    @staticmethod
    def _local_date(value: str | None) -> str:
        if not value:
            return "Unknown date"
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%b %d, %Y")
        except ValueError:
            return value[:10]
