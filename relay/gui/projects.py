"""Phase 4 registered-Projects GUI widgets."""

from __future__ import annotations

import json
import os
from html import escape

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .design_html import td as _td
from .design_html import th_row as _th_row
from .design_tokens import COLORS, METRICS
from .design_typography import apply_type
from .design_widgets import IconButton, LabeledButton
from .json_display import render_json_html
from .scroll_state import preserve_scroll, set_html

# Every row in the Nodes/Connections/Final-outputs tables below can hold a live
# QComboBox picker in at least one column. Qt sizes a row from its tallest cell,
# and a combo box (QSS-forced to METRICS["controlHeight"]) is taller than a
# plain QTableWidgetItem cell (QSS-sized to METRICS["rowHeight"]) - left alone,
# rows with a picker end up a different height than rows without one. Fixing
# every row in these three tables to one explicit height, instead of letting
# Qt compute it per-row, is what actually reconciles the two.
_PICKER_ROW_HEIGHT = METRICS["controlHeight"] + 8


def _format_json(value):
    return render_json_html(value)


def _format_raw_json(value) -> str:
    return f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))}</pre>"


def _render_definition_structured(definition: dict) -> str:
    """A labelled-section summary of a Project definition, not a JSON dump."""
    color = COLORS
    nodes = [n for n in (definition.get("nodes") or []) if isinstance(n, dict)]
    connections = [c for c in (definition.get("connections") or []) if isinstance(c, dict)]
    outputs = [o for o in (definition.get("output_selection") or []) if isinstance(o, dict)]
    orchestrator = definition.get("orchestrator")
    parts: list[str] = []

    description = str(definition.get("description") or "").strip()
    if description:
        parts.append(f'<p style="color:{color["text.secondary"]}">{escape(description)}</p>')

    parts.append(f'<h3 style="color:{color["text.primary"]}">Nodes ({len(nodes)})</h3>')
    if nodes:
        rows = "".join(f"<tr>{_td(n.get('node_id') or '—')}{_td(n.get('task_id') or '—')}</tr>" for n in nodes)
        parts.append(f"<table>{_th_row(['Node ID', 'Task'])}{rows}</table>")
    else:
        parts.append(f'<p style="color:{color["text.muted"]}">No nodes defined.</p>')

    parts.append(f'<h3 style="color:{color["text.primary"]}">Connections ({len(connections)})</h3>')
    if connections:
        rows = "".join(
            f"<tr>{_td(c.get('from_node') or '—')}{_td(c.get('from_role') or '—')}{_td('→')}"
            f"{_td(c.get('to_node') or '—')}{_td(c.get('to_alias') or '—')}</tr>"
            for c in connections
        )
        parts.append(f"<table>{_th_row(['From node', 'Role', '', 'To node', 'Alias'])}{rows}</table>")
    else:
        parts.append(f'<p style="color:{color["text.muted"]}">No connections; nodes run independently.</p>')

    parts.append(f'<h3 style="color:{color["text.primary"]}">Final outputs ({len(outputs)})</h3>')
    if outputs:
        rows = "".join(f"<tr>{_td(o.get('node_id') or '—')}{_td(o.get('role') or '—')}</tr>" for o in outputs)
        parts.append(f"<table>{_th_row(['Node', 'Role'])}{rows}</table>")
    else:
        parts.append(f'<p style="color:{color["text.muted"]}">No final outputs declared.</p>')

    parts.append(f'<h3 style="color:{color["text.primary"]}">Orchestrator</h3>')
    if isinstance(orchestrator, dict) and orchestrator.get("enabled"):
        rows = "".join(f"<tr>{_td(key)}{_td(value)}</tr>" for key, value in orchestrator.items() if key != "enabled")
        parts.append(f"<table>{_th_row(['Setting', 'Value'])}{rows}</table>")
    else:
        parts.append(f'<p style="color:{color["text.muted"]}">Not attached to this Project.</p>')

    failure_policy = str(definition.get("failure_policy") or "").strip()
    if failure_policy:
        parts.append(f'<p style="color:{color["text.secondary"]}">Failure policy: <b>{escape(failure_policy)}</b></p>')

    return "".join(parts)


def _definition_from_project(project):
    raw = project.get("definition_json")
    if not raw:
        return {"name": project.get("name", ""), "nodes": [], "connections": [], "output_selection": []}
    try:
        decoded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        decoded = {}
    if not isinstance(decoded, dict):
        decoded = {}
    decoded.setdefault("name", project.get("name", ""))
    return decoded


class ProjectsListView(QWidget):
    select_project_requested = Signal(str)
    refresh_requested = Signal()
    create_project_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.projects = []
        self.projects_by_id = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel("Projects")
        title.setObjectName("sectionTitle")
        apply_type(title, "title.section")
        header.addWidget(title, 1)
        self.refresh_button = IconButton("refresh", "Refresh the Project list")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self.refresh_button)
        self.create_button = IconButton("plus", "Register a new Project", tone="accent")
        self.create_button.clicked.connect(self.create_project_requested.emit)
        header.addWidget(self.create_button)
        layout.addLayout(header)
        # Own row: this column is narrow, and sharing the header row clipped both
        # the count and the title.
        self.count_label = QLabel("")
        self.count_label.setObjectName("mutedText")
        apply_type(self.count_label, "caption")
        layout.addWidget(self.count_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by name")
        self.search_edit.textChanged.connect(self._rerender)
        layout.addWidget(self.search_edit)
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._item_changed)
        layout.addWidget(self.list_widget, 1)

    def set_projects(self, projects):
        self.projects = list(projects)
        self.projects_by_id = {str(p.get("project_id")): p for p in projects if p.get("project_id")}
        self._rerender()

    def selected_project_id(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _rerender(self):
        with preserve_scroll(self.list_widget):
            self._rerender_content()

    def _rerender_content(self):
        query = self.search_edit.text().strip().casefold()
        self.list_widget.clear()
        visible = 0
        for project in sorted(self.projects, key=lambda row: str(row.get("name") or "").casefold()):
            name = str(project.get("name") or project.get("project_id") or "Project")
            if query and query not in name.casefold():
                continue
            version = project.get("version") or 1
            item = QListWidgetItem(f"{name} · v{int(version)}")
            item.setData(Qt.UserRole, str(project.get("project_id") or ""))
            self.list_widget.addItem(item)
            visible += 1
        total = len(self.projects)
        if not total:
            self.count_label.setText("No registered Projects")
        elif query and visible != total:
            self.count_label.setText(f"{visible} of {total} projects match")
        elif query:
            self.count_label.setText(f"{total} projects match")
        elif total >= 200:
            self.count_label.setText(f"{total} projects (server may have more)")
        else:
            self.count_label.setText(f"{total} projects")

    def _item_activated(self, item):
        project_id = item.data(Qt.UserRole)
        if project_id:
            self.select_project_requested.emit(str(project_id))

    def _item_changed(self, item, _previous):
        if item is not None:
            self._item_activated(item)


class ProjectDetailView(QWidget):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    run_requested = Signal(str)
    refresh_requested = Signal(str)
    run_link_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project_id = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Project")
        self.title_label.setObjectName("pageTitle")
        apply_type(self.title_label, "title.detail")
        header.addWidget(self.title_label, 1)
        self.status_label = QLabel("")
        header.addWidget(self.status_label)
        self.refresh_button = IconButton("refresh", "Refresh this Project")
        self.refresh_button.clicked.connect(self._on_refresh)
        header.addWidget(self.refresh_button)
        self.edit_button = IconButton("pencil", "Edit this Project")
        self.edit_button.clicked.connect(self._on_edit)
        header.addWidget(self.edit_button)
        self.delete_button = IconButton("trash", "Delete this Project", tone="danger")
        self.delete_button.clicked.connect(self._on_delete)
        header.addWidget(self.delete_button)
        # Run is the one primary action on this screen; it gets a filled,
        # labelled button so it visibly outweighs the three quiet icon actions
        # instead of reading as a same-weight fourth square.
        self.run_button = LabeledButton("play", "Run", tone="primary")
        self.run_button.clicked.connect(self._on_run)
        header.addWidget(self.run_button)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.overview_browser = QTextBrowser()
        definition_tab = QWidget()
        definition_layout = QVBoxLayout(definition_tab)
        definition_layout.setContentsMargins(0, 0, 0, 0)
        definition_toolbar = QHBoxLayout()
        definition_toolbar.addStretch(1)
        self.definition_view_toggle = QPushButton("View raw JSON")
        self.definition_view_toggle.clicked.connect(self._on_toggle_definition_view)
        definition_toolbar.addWidget(self.definition_view_toggle)
        definition_layout.addLayout(definition_toolbar)
        self.definition_browser = QTextBrowser()
        definition_layout.addWidget(self.definition_browser, 1)
        self.runs_browser = QTextBrowser()
        self.runs_browser.setOpenLinks(False)
        self.runs_browser.anchorClicked.connect(self._on_run_link)
        self.tabs.addTab(self.overview_browser, "Overview")
        self.tabs.addTab(definition_tab, "Definition")
        self.tabs.addTab(self.runs_browser, "Runs")
        layout.addWidget(self.tabs, 1)
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)
        self._current_definition: dict = {}
        self._definition_raw = False

    def set_project(self, project, runs=None):
        self.project_id = str(project.get("project_id") or "") or None
        self.title_label.setText(str(project.get("name") or self.project_id or "Project"))
        version = int(project.get("version") or 1)
        deleted_at = project.get("deleted_at")
        status = "Deleted" if deleted_at else "Active"
        self.status_label.setText(f"v{version} · {status}")
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(self.project_id is not None and not deleted_at)
        self._current_definition = _definition_from_project(project)
        self._render_definition_view()
        set_html(
            self.overview_browser,
            _format_json(
                {
                    "Project ID": project.get("project_id"),
                    "Name": project.get("name"),
                    "Description": project.get("description"),
                    "Version": project.get("version"),
                    "Created": project.get("created_at"),
                    "Updated": project.get("updated_at"),
                    "Deleted": deleted_at,
                }
            ),
        )
        set_html(self.runs_browser, self._format_runs(runs or []))

    def clear(self):
        self.project_id = None
        self.title_label.setText("Project")
        self.status_label.setText("")
        self._current_definition = {}
        self._definition_raw = False
        self.definition_view_toggle.setText("View raw JSON")
        for browser in (self.overview_browser, self.definition_browser, self.runs_browser):
            with preserve_scroll(browser):
                browser.clear()
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_runs(self, runs):
        set_html(self.runs_browser, self._format_runs(runs))

    def _render_definition_view(self):
        if self._definition_raw:
            set_html(self.definition_browser, _format_raw_json(self._current_definition))
        else:
            set_html(self.definition_browser, _render_definition_structured(self._current_definition))

    def _on_toggle_definition_view(self):
        self._definition_raw = not self._definition_raw
        self.definition_view_toggle.setText("View structured" if self._definition_raw else "View raw JSON")
        self._render_definition_view()

    @staticmethod
    def _format_runs(runs):
        if not runs:
            return "<i>No Project Runs recorded for this Project yet.</i>"
        rows = "".join(
            f"<tr>{_td(ProjectDetailView._run_link(run))}{_td(run.get('status') or '—')}"
            f"{_td(run.get('created_at') or '—')}{_td(run.get('trigger_type') or '—')}</tr>"
            for run in runs
        )
        return f"<table>{_th_row(['Run', 'Status', 'Created', 'Trigger'])}{rows}</table>"

    @staticmethod
    def _run_link(run) -> str:
        run_id = str(run.get("project_run_id") or "—")
        if run_id == "—":
            return escape(run_id)
        return f'<a href="relay://project-run/{escape(run_id)}">{escape(run_id)}</a>'

    def _on_run_link(self, url) -> None:
        if url.scheme() == "relay" and url.host() == "project-run":
            run_id = url.path().lstrip("/")
            if run_id:
                self.run_link_requested.emit(run_id)

    def _on_refresh(self):
        if self.project_id:
            self.refresh_requested.emit(self.project_id)

    def _on_run(self):
        if self.project_id:
            self.run_requested.emit(self.project_id)

    def _on_edit(self):
        if self.project_id:
            self.edit_requested.emit(self.project_id)

    def _on_delete(self):
        if self.project_id:
            self.delete_requested.emit(self.project_id)


class _PickerComboBox(QComboBox):
    """A QComboBox whose ``sizeHint`` height is capped at ``METRICS["controlHeight"]``.

    ``setFixedHeight`` alone does *not* fix the picker-row-height mismatch:
    ``QComboBox.sizeHint()`` grows with whatever font Qt falls back to for the
    current item text - a real Task name in a CJK script (e.g. Korean) can hit
    a taller fallback font than plain ASCII text, reporting a sizeHint of
    ~46px even though ``setFixedHeight(28)`` was called. Qt's table view sizes
    and places `setCellWidget` editors from that reported ``sizeHint()``, not
    from the widget's actual height policy, so the combo still rendered at its
    full ~46px and visibly bled into the row below. Overriding ``sizeHint``
    itself is what Qt's row-placement logic actually reads.
    """

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        hint = super().sizeHint()
        return QSize(hint.width(), METRICS["controlHeight"])


class ReviewGateDialog(QDialog):
    """Small progressive-disclosure editor for a node's result review gate."""

    def __init__(self, review: dict | None = None, *, parent=None) -> None:
        super().__init__(parent)
        review = review or {}
        self.setWindowTitle("Result review gate")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "The node completes first. Its result is shown in the Review workspace, "
            "then the Project continues only after confirmation."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedText")
        layout.addWidget(hint)
        form = QFormLayout()
        self.enabled = QCheckBox("Require review for this node")
        self.enabled.setChecked(bool(review.get("enabled")))
        form.addRow("Review gate", self.enabled)
        self.reviewer = QComboBox()
        self.reviewer.addItem("Human", "human")
        self.reviewer.addItem("Orchestrator", "orchestrator")
        self.reviewer.setCurrentIndex(1 if review.get("reviewer") == "orchestrator" else 0)
        form.addRow("Reviewer", self.reviewer)
        self.guidelines = QTextEdit()
        self.guidelines.setAcceptRichText(False)
        self.guidelines.setPlaceholderText(
            "What should be checked, from which perspective, and what counts as acceptable?"
        )
        self.guidelines.setPlainText(str(review.get("guidelines") or ""))
        self.guidelines.setMaximumHeight(110)
        form.addRow("Review guidelines", self.guidelines)
        self.max_reruns = QSpinBox()
        self.max_reruns.setRange(0, 20)
        self.max_reruns.setValue(int(review.get("max_reruns") if review.get("max_reruns") is not None else 2))
        self.max_reruns.setToolTip("After this many automatic reruns, the result is handed to a human.")
        form.addRow("Max automatic reruns", self.max_reruns)
        layout.addLayout(form)
        self.error = QLabel()
        self.error.setObjectName("errorText")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        if (
            self.enabled.isChecked()
            and self.reviewer.currentData() == "orchestrator"
            and not self.guidelines.toPlainText().strip()
        ):
            self.error.setText("Orchestrator review requires review guidelines.")
            return
        self.accept()

    def payload(self) -> dict:
        if not self.enabled.isChecked():
            return {}
        return {
            "enabled": True,
            "reviewer": self.reviewer.currentData(),
            "guidelines": self.guidelines.toPlainText().strip() or None,
            "max_reruns": int(self.max_reruns.value()),
        }


class ProjectEditorDialog(QDialog):
    accepted_payload = Signal(dict)

    def __init__(
        self,
        *,
        project=None,
        available_tasks=None,
        delivery_roots=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Project" if project else "New Project")
        self.resize(1040, 780)
        # (task_id, name) pairs, not a free-text label the user has to retype: the
        # Task column below is a picker built from this, never hand-typed.
        self._task_options = sorted(
            (
                (str(task.get("task_id")), str(task.get("name") or task.get("task_id")))
                for task in (available_tasks or [])
                if task.get("task_id")
            ),
            key=lambda pair: pair[1].casefold(),
        )
        self._delivery_roots = delivery_roots or []
        self._saving = False

        dialog_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        root = QVBoxLayout(body)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Project name", self.name_edit)
        self.description_edit = QLineEdit()
        form.addRow("Description", self.description_edit)
        root.addLayout(form)

        if not self._task_options:
            no_tasks_hint = QLabel(
                "No Tasks are registered yet. Register a Task first (sidebar &rarr; Tasks) - "
                "a Project node can only run an existing Task."
            )
            no_tasks_hint.setWordWrap(True)
            no_tasks_hint.setObjectName("errorText")
            root.addWidget(no_tasks_hint)

        root.addWidget(QLabel("<b>Nodes</b>"))
        self.nodes_table = QTableWidget(0, 4)
        self.nodes_table.setHorizontalHeaderLabels(["Node ID", "Task", "Checkpoint (JSON)", "Review gate"])
        nodes_header = self.nodes_table.horizontalHeader()
        nodes_header.setSectionResizeMode(0, QHeaderView.Interactive)
        nodes_header.setSectionResizeMode(1, QHeaderView.Stretch)
        nodes_header.setSectionResizeMode(2, QHeaderView.Interactive)
        nodes_header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.nodes_table.setColumnWidth(0, 180)
        self.nodes_table.setColumnWidth(2, 220)
        self.nodes_table.setColumnWidth(3, 116)
        self.nodes_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.nodes_table.setMinimumHeight(140)
        self.nodes_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.nodes_table.verticalHeader().setDefaultSectionSize(_PICKER_ROW_HEIGHT)
        self.nodes_table.itemChanged.connect(lambda item: item.setToolTip(item.text()))
        root.addWidget(self.nodes_table)
        node_buttons = QHBoxLayout()
        self.add_node_button = IconButton("plus", "Add a node")
        self.add_node_button.clicked.connect(self._on_add_node)
        self.remove_node_button = IconButton("minus", "Remove the selected node")
        self.remove_node_button.clicked.connect(self._on_remove_node)
        node_buttons.addWidget(self.add_node_button)
        node_buttons.addWidget(self.remove_node_button)
        node_buttons.addStretch(1)
        root.addLayout(node_buttons)

        root.addWidget(QLabel("<b>Connections</b> (from node, role -> to node, alias A1/A2/...)"))
        conn_hint = QLabel(
            "Role is the Artifact role the source node's Task declares (its own output, "
            "or the reserved name <code>result</code>). Alias must look like A1, A2, ..."
        )
        conn_hint.setWordWrap(True)
        conn_hint.setObjectName("mutedText")
        apply_type(conn_hint, "caption")
        root.addWidget(conn_hint)
        self.connections_table = QTableWidget(0, 4)
        self.connections_table.setHorizontalHeaderLabels(["From node", "Role", "To node", "Alias"])
        conn_header = self.connections_table.horizontalHeader()
        conn_header.setSectionResizeMode(0, QHeaderView.Interactive)
        conn_header.setSectionResizeMode(1, QHeaderView.Stretch)
        conn_header.setSectionResizeMode(2, QHeaderView.Interactive)
        conn_header.setSectionResizeMode(3, QHeaderView.Interactive)
        self.connections_table.setColumnWidth(0, 180)
        self.connections_table.setColumnWidth(2, 180)
        self.connections_table.setColumnWidth(3, 100)
        self.connections_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.connections_table.setMinimumHeight(140)
        self.connections_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.connections_table.verticalHeader().setDefaultSectionSize(_PICKER_ROW_HEIGHT)
        self.connections_table.itemChanged.connect(lambda item: item.setToolTip(item.text()))
        root.addWidget(self.connections_table)
        conn_buttons = QHBoxLayout()
        self.add_conn_button = IconButton("plus", "Add a connection")
        self.add_conn_button.clicked.connect(self._on_add_connection)
        self.remove_conn_button = IconButton("minus", "Remove the selected connection")
        self.remove_conn_button.clicked.connect(self._on_remove_connection)
        conn_buttons.addWidget(self.add_conn_button)
        conn_buttons.addWidget(self.remove_conn_button)
        conn_buttons.addStretch(1)
        root.addLayout(conn_buttons)

        root.addWidget(QLabel("<b>Final outputs</b> (node_id, role)"))
        self.outputs_table = QTableWidget(0, 2)
        self.outputs_table.setHorizontalHeaderLabels(["Node", "Role"])
        outputs_header = self.outputs_table.horizontalHeader()
        outputs_header.setSectionResizeMode(0, QHeaderView.Interactive)
        outputs_header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.outputs_table.setColumnWidth(0, 220)
        self.outputs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.outputs_table.setMinimumHeight(110)
        self.outputs_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.outputs_table.verticalHeader().setDefaultSectionSize(_PICKER_ROW_HEIGHT)
        self.outputs_table.itemChanged.connect(lambda item: item.setToolTip(item.text()))
        root.addWidget(self.outputs_table)
        output_buttons = QHBoxLayout()
        self.add_output_button = IconButton("plus", "Add a final output")
        self.add_output_button.clicked.connect(self._on_add_output)
        self.remove_output_button = IconButton("minus", "Remove the selected output")
        self.remove_output_button.clicked.connect(self._on_remove_output)
        output_buttons.addWidget(self.add_output_button)
        output_buttons.addWidget(self.remove_output_button)
        output_buttons.addStretch(1)
        root.addLayout(output_buttons)

        root.addWidget(QLabel("<b>Orchestrator</b> (optional)"))
        orch_hint = QLabel(
            "On failure, narrates progress and repairs within a bounded budget (retry, worker "
            "swap, connection/output role rebind, an append-only instruction addendum) before "
            "reporting the cause. Its authority is a strict subset of what you can already do "
            "through this editor and the Project Runs screen, and it never leaves the Run - the "
            "Project and Task definitions here are never changed by it."
        )
        orch_hint.setWordWrap(True)
        orch_hint.setObjectName("mutedText")
        apply_type(orch_hint, "caption")
        root.addWidget(orch_hint)
        self._original_orchestrator: dict = {}
        self.orchestrator_enabled_checkbox = QCheckBox("Attach an Orchestrator to this Project's Runs")
        root.addWidget(self.orchestrator_enabled_checkbox)
        orch_form = QFormLayout()
        self.orchestrator_worker_edit = QLineEdit()
        self.orchestrator_worker_edit.setPlaceholderText("auto")
        orch_form.addRow("Worker (Orchestrator's own reasoning)", self.orchestrator_worker_edit)
        self.orchestrator_model_edit = QLineEdit()
        self.orchestrator_model_edit.setPlaceholderText("worker default, e.g. gpt-5.6-luna")
        orch_form.addRow("Model", self.orchestrator_model_edit)
        self.orchestrator_profile_edit = QLineEdit()
        orch_form.addRow("Profile", self.orchestrator_profile_edit)
        self.orchestrator_max_repairs_node_spin = QSpinBox()
        self.orchestrator_max_repairs_node_spin.setRange(1, 20)
        self.orchestrator_max_repairs_node_spin.setValue(2)
        orch_form.addRow("Max repairs per node", self.orchestrator_max_repairs_node_spin)
        self.orchestrator_max_repairs_run_spin = QSpinBox()
        self.orchestrator_max_repairs_run_spin.setRange(1, 50)
        self.orchestrator_max_repairs_run_spin.setValue(6)
        orch_form.addRow("Max repairs per Run", self.orchestrator_max_repairs_run_spin)
        self.orchestrator_max_llm_calls_spin = QSpinBox()
        self.orchestrator_max_llm_calls_spin.setRange(1, 50)
        self.orchestrator_max_llm_calls_spin.setValue(8)
        orch_form.addRow("Max agent calls per Run", self.orchestrator_max_llm_calls_spin)
        root.addLayout(orch_form)

        root.addStretch(1)

        scroll.setWidget(body)
        dialog_layout.addWidget(scroll, 1)

        # Kept outside the scroll area on purpose: the error message and the Save/
        # Cancel buttons must stay reachable no matter how many rows the tables
        # above grow to, instead of being pushed off the bottom of a fixed-size
        # dialog with nothing to scroll it into view.
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("errorText")
        dialog_layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        self.save_button = self.buttons.button(QDialogButtonBox.Save)
        self.cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._on_save)
        self.buttons.rejected.connect(self.reject)
        dialog_layout.addWidget(self.buttons)
        if project:
            self._populate(project)

    def show_error(self, message):
        self.error_label.setText(message)

    def set_saving(self, saving: bool) -> None:
        """Toggle the in-flight-save state. Called by MainWindow around the POST."""
        self._saving = saving
        self.save_button.setEnabled(not saving)
        self.save_button.setText("Saving..." if saving else "Save")
        self.cancel_button.setEnabled(not saving)

    def report_save_error(self, message: str) -> None:
        """Backend rejected the save: re-open for editing, keep every typed row."""
        self.set_saving(False)
        self.show_error(message)

    def close_after_save(self) -> None:
        """Backend confirmed the save: only now is it safe to close the dialog."""
        self.accept()

    def _populate(self, project):
        self.name_edit.setText(str(project.get("name") or ""))
        self.description_edit.setText(str(project.get("description") or ""))
        definition = _definition_from_project(project)
        self._populate_nodes(definition.get("nodes") or [])
        self._populate_connections(definition.get("connections") or [])
        self._populate_outputs(definition.get("output_selection") or [])
        self._populate_orchestrator(definition.get("orchestrator") or {})

    def _populate_orchestrator(self, orchestrator: dict) -> None:
        self._original_orchestrator = dict(orchestrator)
        self.orchestrator_enabled_checkbox.setChecked(bool(orchestrator.get("enabled")))
        self.orchestrator_worker_edit.setText(str(orchestrator.get("worker") or ""))
        self.orchestrator_model_edit.setText(str(orchestrator.get("model") or ""))
        self.orchestrator_profile_edit.setText(str(orchestrator.get("profile") or ""))
        self.orchestrator_max_repairs_node_spin.setValue(int(orchestrator.get("max_repair_attempts_per_node") or 2))
        self.orchestrator_max_repairs_run_spin.setValue(int(orchestrator.get("max_repair_attempts_per_run") or 6))
        self.orchestrator_max_llm_calls_spin.setValue(int(orchestrator.get("max_llm_calls_per_run") or 8))

    def _populate_nodes(self, nodes):
        self.nodes_table.setRowCount(0)
        for node in nodes:
            row = self.nodes_table.rowCount()
            self.nodes_table.insertRow(row)
            self._set_cell(self.nodes_table, row, 0, str(node.get("node_id") or ""))
            self.nodes_table.setCellWidget(row, 1, self._build_task_combo(str(node.get("task_id") or "")))
            checkpoint = node.get("checkpoint") or {}
            if isinstance(checkpoint, dict) and checkpoint:
                self._set_cell(self.nodes_table, row, 2, json.dumps(checkpoint))
            else:
                self._set_cell(self.nodes_table, row, 2, "")
            self._set_review_button(row)

    def _populate_connections(self, connections):
        self.connections_table.setRowCount(0)
        for connection in connections:
            row = self.connections_table.rowCount()
            self.connections_table.insertRow(row)
            self.connections_table.setCellWidget(row, 0, self._build_node_combo(str(connection.get("from_node") or "")))
            self._set_cell(
                self.connections_table,
                row,
                1,
                str(connection.get("from_role") or ""),
                tooltip="Artifact role produced by the from-node's Task.",
            )
            self.connections_table.setCellWidget(row, 2, self._build_node_combo(str(connection.get("to_node") or "")))
            self._set_cell(
                self.connections_table,
                row,
                3,
                str(connection.get("to_alias") or ""),
                tooltip="Matches ^A[1-9][0-9]*$, e.g. A1, A2.",
            )

    def _populate_outputs(self, outputs):
        self.outputs_table.setRowCount(0)
        for output in outputs:
            row = self.outputs_table.rowCount()
            self.outputs_table.insertRow(row)
            self.outputs_table.setCellWidget(row, 0, self._build_node_combo(str(output.get("node_id") or "")))
            self._set_cell(
                self.outputs_table,
                row,
                1,
                str(output.get("role") or ""),
                tooltip="Artifact role this final output must match.",
            )

    def _current_node_ids(self) -> list[str]:
        seen: list[str] = []
        for row in range(self.nodes_table.rowCount()):
            node_id = self._row_text(self.nodes_table, row, 0)
            if node_id and node_id not in seen:
                seen.append(node_id)
        return seen

    def _build_task_combo(self, selected_task_id: str = "") -> QComboBox:
        """A picker, not a field the user has to hand-type a Task ID into."""
        combo = _PickerComboBox()
        combo.setEditable(False)
        combo.setFixedHeight(METRICS["controlHeight"])
        found = False
        for task_id, name in self._task_options:
            combo.addItem(name, task_id)
            combo.setItemData(combo.count() - 1, f"{name} ({task_id})", Qt.ToolTipRole)
            if task_id == selected_task_id:
                found = True
        if selected_task_id and not found:
            # The stored task_id no longer matches a registered Task (e.g. it was
            # deleted after this Project was defined). Keep it visible and selected
            # instead of silently swapping in an unrelated Task the next time this
            # dialog is saved.
            combo.insertItem(0, f"(missing Task) {selected_task_id}", selected_task_id)
            combo.setItemData(0, f"Task {selected_task_id} is no longer registered.", Qt.ToolTipRole)
        if selected_task_id:
            index = combo.findData(selected_task_id)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.currentIndexChanged.connect(lambda _i, c=combo: c.setToolTip(c.currentData(Qt.ToolTipRole) or ""))
        combo.setToolTip(combo.currentData(Qt.ToolTipRole) or "")
        return combo

    def _build_node_combo(self, selected_node_id: str = "") -> QComboBox:
        """Editable picker over the node_ids already typed in the Nodes table above,
        so a connection/output can't silently reference a node that doesn't exist."""
        combo = _PickerComboBox()
        combo.setEditable(True)
        combo.setFixedHeight(METRICS["controlHeight"])
        combo.addItems(self._current_node_ids())
        combo.setCurrentText(selected_node_id)
        combo.setPlaceholderText("node_id")
        return combo

    @staticmethod
    def _set_cell(table, row, column, text, *, tooltip: str | None = None):
        item = QTableWidgetItem(text)
        item.setToolTip(tooltip if tooltip is not None else text)
        table.setItem(row, column, item)

    def _on_add_node(self):
        row = self.nodes_table.rowCount()
        self.nodes_table.insertRow(row)
        self._set_cell(self.nodes_table, row, 0, "", tooltip="Unique within this Project, e.g. research.")
        self.nodes_table.setCellWidget(row, 1, self._build_task_combo())
        self._set_cell(self.nodes_table, row, 2, "")
        self._set_review_button(row)

    def _on_remove_node(self):
        rows = sorted({item.row() for item in self.nodes_table.selectedIndexes()}, reverse=True)
        for index in rows:
            self.nodes_table.removeRow(index)

    def _on_add_connection(self):
        row = self.connections_table.rowCount()
        self.connections_table.insertRow(row)
        self.connections_table.setCellWidget(row, 0, self._build_node_combo())
        self._set_cell(self.connections_table, row, 1, "", tooltip="Artifact role produced by the from-node's Task.")
        self.connections_table.setCellWidget(row, 2, self._build_node_combo())
        self._set_cell(self.connections_table, row, 3, "", tooltip="Matches ^A[1-9][0-9]*$, e.g. A1, A2.")

    def _on_remove_connection(self):
        rows = sorted({item.row() for item in self.connections_table.selectedIndexes()}, reverse=True)
        for index in rows:
            self.connections_table.removeRow(index)

    def _on_add_output(self):
        row = self.outputs_table.rowCount()
        self.outputs_table.insertRow(row)
        self.outputs_table.setCellWidget(row, 0, self._build_node_combo())
        self._set_cell(self.outputs_table, row, 1, "", tooltip="Artifact role this final output must match.")

    def _on_remove_output(self):
        rows = sorted({item.row() for item in self.outputs_table.selectedIndexes()}, reverse=True)
        for index in rows:
            self.outputs_table.removeRow(index)

    def _on_save(self):
        if self._saving:
            return
        try:
            payload = self.payload()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.show_error("")
        self.set_saving(True)
        self.accepted_payload.emit(payload)
        # Deliberately does not close the dialog: MainWindow calls close_after_save()
        # only once the daemon confirms the write, and report_save_error() otherwise
        # so the user never loses what they typed to a rejected save.

    def payload(self):
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Project name is required.")
        nodes = []
        for row in range(self.nodes_table.rowCount()):
            node_id = self._row_text(self.nodes_table, row, 0)
            task_combo = self.nodes_table.cellWidget(row, 1)
            checkpoint_text = self._row_text(self.nodes_table, row, 2)
            if not node_id:
                raise ValueError(f"Node {row + 1} has an empty node_id.")
            task_id = task_combo.currentData() if isinstance(task_combo, QComboBox) else None
            if not task_id:
                raise ValueError(f"Node {row + 1} must select a Task.")
            node = {"node_id": node_id, "task_id": task_id}
            checkpoint = self._parse_checkpoint(checkpoint_text)
            if checkpoint:
                node["checkpoint"] = checkpoint
            nodes.append(node)
        if not nodes:
            raise ValueError("A Project must declare at least one node.")
        connections = []
        for row in range(self.connections_table.rowCount()):
            from_node = self._combo_text(self.connections_table, row, 0)
            from_role = self._row_text(self.connections_table, row, 1)
            to_node = self._combo_text(self.connections_table, row, 2)
            to_alias = self._row_text(self.connections_table, row, 3)
            if not (from_node and from_role and to_node and to_alias):
                continue
            connections.append(
                {
                    "from_node": from_node,
                    "from_role": from_role,
                    "to_node": to_node,
                    "to_alias": to_alias,
                }
            )
        output_selection = []
        for row in range(self.outputs_table.rowCount()):
            node_id = self._combo_text(self.outputs_table, row, 0)
            role = self._row_text(self.outputs_table, row, 1)
            if not (node_id and role):
                continue
            output_selection.append({"node_id": node_id, "role": role})
        result = {
            "name": name,
            "description": self.description_edit.text().strip() or None,
            "failure_policy": "stop",
            "nodes": nodes,
            "connections": connections,
            "output_selection": output_selection,
        }
        orchestrator = self._orchestrator_payload()
        if orchestrator is not None:
            result["orchestrator"] = orchestrator
        return result

    def _set_review_button(self, row: int) -> None:
        button = QPushButton("Configure…")
        button.clicked.connect(lambda _checked=False, current_row=row: self._configure_review(current_row))
        self.nodes_table.setCellWidget(row, 3, button)

    def _configure_review(self, row: int) -> None:
        checkpoint = self._parse_checkpoint(self._row_text(self.nodes_table, row, 2)) or {}
        review = {
            key: checkpoint.get(key) for key in ("enabled", "reviewer", "guidelines", "max_reruns") if key in checkpoint
        }
        dialog = ReviewGateDialog(review, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        for key in ("enabled", "reviewer", "guidelines", "max_reruns"):
            checkpoint.pop(key, None)
        checkpoint.update(dialog.payload())
        self._set_cell(self.nodes_table, row, 2, json.dumps(checkpoint) if checkpoint else "")

    def _orchestrator_payload(self) -> dict | None:
        """None means "omit the key" - a brand-new Project that never enabled the
        Orchestrator gets a snapshot with no orchestrator key at all, matching the
        byte-for-byte-unchanged invariant. A Project that had one attached keeps its
        settings on the payload even while unchecked, so re-enabling doesn't lose them.
        """
        enabled = self.orchestrator_enabled_checkbox.isChecked()
        if not enabled and not self._original_orchestrator:
            return None
        orchestrator = dict(self._original_orchestrator)
        orchestrator["enabled"] = enabled
        worker = self.orchestrator_worker_edit.text().strip()
        if worker:
            orchestrator["worker"] = worker
        else:
            orchestrator.pop("worker", None)
        model = self.orchestrator_model_edit.text().strip()
        if model:
            orchestrator["model"] = model
        else:
            orchestrator.pop("model", None)
        profile = self.orchestrator_profile_edit.text().strip()
        if profile:
            orchestrator["profile"] = profile
        else:
            orchestrator.pop("profile", None)
        orchestrator["max_repair_attempts_per_node"] = self.orchestrator_max_repairs_node_spin.value()
        orchestrator["max_repair_attempts_per_run"] = self.orchestrator_max_repairs_run_spin.value()
        orchestrator["max_llm_calls_per_run"] = self.orchestrator_max_llm_calls_spin.value()
        return orchestrator

    @staticmethod
    def _row_text(table, row, column):
        item = table.item(row, column)
        return item.text().strip() if item else ""

    @staticmethod
    def _combo_text(table, row, column):
        combo = table.cellWidget(row, column)
        return combo.currentText().strip() if isinstance(combo, QComboBox) else ""

    def _parse_checkpoint(self, text):
        text = text.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Checkpoint must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Checkpoint must be a JSON object.")
        deliver_to = parsed.get("deliver_to") or []
        if deliver_to:
            for item in deliver_to:
                if not isinstance(item, dict) or str(item.get("kind") or "").strip() != "folder":
                    raise ValueError("Only folder delivery targets are supported right now.")
                if not self._delivery_root_contains(str(item.get("path") or "").strip()):
                    raise ValueError(f"Delivery path is not in allow-list: {item.get('path')}")
        return parsed

    def _delivery_root_contains(self, path):
        if not path:
            return False
        normalized = os.path.normcase(os.path.abspath(path))
        for root in self._delivery_roots:
            try:
                normalized_root = os.path.normcase(os.path.abspath(root))
            except (OSError, ValueError):
                continue
            if normalized == normalized_root or normalized.startswith(normalized_root + os.sep):
                return True
        return False


class ProjectRunMonitorDialog(QDialog):
    accepted_action = Signal(str, dict)

    def __init__(
        self,
        *,
        project_run_id,
        project_run=None,
        steps=None,
        nodes=None,
        parent=None,
    ):
        super().__init__(parent)
        self.project_run_id = project_run_id
        self._project_run = project_run or {}
        self._steps = list(steps or [])
        self._nodes = list(nodes or [])
        self.setWindowTitle(f"Project Run - {project_run_id}")
        self.resize(720, 540)
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>Project Run {project_run_id}</b>"), 1)
        root.addLayout(header)
        self.status_label = QLabel("Loading...")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        root.addWidget(QLabel("<b>Steps</b>"))
        self.steps_browser = QTextBrowser()
        root.addWidget(self.steps_browser, 3)
        action_row = QHBoxLayout()
        self.refresh_button = IconButton("refresh", "Refresh this Project Run")
        self.refresh_button.clicked.connect(
            lambda: self.accepted_action.emit("refresh", {"project_run_id": self.project_run_id})
        )
        action_row.addWidget(self.refresh_button)
        self.cancel_button = IconButton("stop", "Cancel this Project Run", tone="danger")
        self.cancel_button.clicked.connect(
            lambda: self.accepted_action.emit("cancel", {"project_run_id": self.project_run_id})
        )
        action_row.addWidget(self.cancel_button)
        action_row.addStretch(1)
        root.addLayout(action_row)
        reexec_label = QLabel("Partial reexecute from node (with cascade):")
        root.addWidget(reexec_label)
        reexec_row = QHBoxLayout()
        self.reexec_node_edit = QLineEdit()
        self.reexec_node_edit.setPlaceholderText("e.g. analyze")
        reexec_row.addWidget(self.reexec_node_edit, 1)
        self.cascade_checkbox = QCheckBox("Cascade to descendants")
        self.cascade_checkbox.setChecked(True)
        reexec_row.addWidget(self.cascade_checkbox)
        self.reexec_button = QPushButton("Reexecute from node")
        self.reexec_button.clicked.connect(self._submit_reexec)
        reexec_row.addWidget(self.reexec_button)
        root.addLayout(reexec_row)
        self.action_help = QLabel(
            "Use Cancel for terminal failure cancellation. Reexecute re-runs only the selected node."
        )
        self.action_help.setWordWrap(True)
        root.addWidget(self.action_help)
        self._render(project_run or {}, steps or [], nodes or [])

    def set_project_run(self, project_run):
        self._project_run = project_run or {}
        self._render(self._project_run, self._steps, self._nodes)

    def set_steps(self, steps):
        self._steps = list(steps or [])
        self._render(self._project_run, self._steps, self._nodes)

    def _render(self, run, steps, nodes):
        status = run.get("status") or "unknown"
        trigger = run.get("trigger_type") or "-"
        created = run.get("created_at") or "-"
        self.status_label.setText(f"Status: {status} - Trigger: {trigger} - Created: {created}")
        node_order = {node.get("node_id"): i for i, node in enumerate(nodes or [])}
        ordered = sorted(
            steps,
            key=lambda step: (node_order.get(step.get("node_id"), 99), step.get("node_id") or ""),
        )
        if not ordered:
            set_html(self.steps_browser, "<i>No step telemetry yet for this Project Run.</i>")
            return
        rows = "".join(
            f"<tr>{_td(step.get('node_id') or '-')}{_td(step.get('task_id') or '-')}"
            f"{_td(step.get('status') or '-')}{_td(step.get('error_code') or '-')}</tr>"
            for step in ordered
        )
        set_html(self.steps_browser, f"<table>{_th_row(['Node', 'Task', 'Status', 'Error'])}{rows}</table>")

    def _submit_reexec(self):
        node_id = self.reexec_node_edit.text().strip()
        if not node_id:
            self.action_help.setText("Enter the node ID to reexecute from.")
            return
        self.accepted_action.emit(
            "partial-reexecute",
            {
                "project_run_id": self.project_run_id,
                "from_node": node_id,
                "cascade": self.cascade_checkbox.isChecked(),
            },
        )


class ProjectsView(QWidget):
    refresh_requested = Signal()
    create_requested = Signal()
    select_project_requested = Signal(str)
    edit_project_requested = Signal(str)
    delete_project_requested = Signal(str)
    run_project_requested = Signal(str)
    project_create_submitted = Signal(dict)
    project_edit_submitted = Signal(str, dict)
    project_run_submitted = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.projects_index = {}
        self.tasks_index = {}
        self.delivery_roots = []
        self.editor = None
        self.run_dialog = None
        # No section heading here: the top bar names the section and the list
        # column carries its own title.
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        self.list = ProjectsListView()
        self.list.refresh_requested.connect(self.refresh_requested.emit)
        self.list.create_project_requested.connect(self.create_requested.emit)
        self.list.select_project_requested.connect(self.select_project_requested.emit)
        self.list.setMaximumWidth(280)
        body.addWidget(self.list)
        self.detail = ProjectDetailView()
        self.detail.refresh_requested.connect(lambda pid: self.select_project_requested.emit(pid))
        self.detail.edit_requested.connect(self.edit_project_requested.emit)
        self.detail.delete_requested.connect(self.delete_project_requested.emit)
        self.detail.run_requested.connect(self.run_project_requested.emit)
        body.addWidget(self.detail, 1)
        root.addLayout(body, 1)
        self.set_projects([])

    def set_projects(self, projects):
        self.projects_index = {str(p.get("project_id")): p for p in projects if p.get("project_id")}
        self.list.set_projects(projects)

    def set_project(self, project, runs=None):
        self.projects_index[str(project.get("project_id") or "")] = project
        self.detail.set_project(project, runs)

    def set_runs(self, project_id, runs):
        if self.detail.project_id == project_id:
            self.detail.set_runs(runs)

    def set_tasks(self, tasks):
        self.tasks_index = {str(t.get("task_id")): t for t in tasks if t.get("task_id")}

    def set_delivery_roots(self, roots):
        self.delivery_roots = list(roots)

    def show_create_editor(self):
        self.editor = ProjectEditorDialog(
            available_tasks=list(self.tasks_index.values()),
            delivery_roots=self.delivery_roots,
            parent=self,
        )
        self.editor.accepted_payload.connect(self.project_create_submitted.emit)
        # accepted only fires from close_after_save(); rejected fires on Cancel.
        # Either way the dialog is done, so drop the reference MainWindow checks
        # before delivering a save result back into it.
        self.editor.accepted.connect(self._clear_editor)
        self.editor.rejected.connect(self._clear_editor)
        self.editor.open()

    def show_edit_editor(self, project_id):
        project = self.projects_index.get(project_id)
        if not project:
            return
        self.editor = ProjectEditorDialog(
            project=project,
            available_tasks=list(self.tasks_index.values()),
            delivery_roots=self.delivery_roots,
            parent=self,
        )
        self.editor.accepted_payload.connect(
            lambda payload, pid=project_id: self.project_edit_submitted.emit(pid, payload)
        )
        self.editor.accepted.connect(self._clear_editor)
        self.editor.rejected.connect(self._clear_editor)
        self.editor.open()

    def _clear_editor(self):
        self.editor = None

    def show_run_dialog(self, project_id):
        project = self.projects_index.get(project_id) or {}
        definition = _definition_from_project(project)
        nodes = definition.get("nodes") or []
        self.run_dialog = ProjectRunMonitorDialog(
            project_run_id=f"pending-{project_id}",
            project_run={"status": "preview", "trigger_type": "-"},
            steps=[],
            nodes=nodes,
            parent=self,
        )
        self.run_dialog.accepted_action.connect(self._on_run_action)
        self.run_dialog.open()

    def _on_run_action(self, action, payload):
        if action == "refresh":
            project_run_id = payload.get("project_run_id")
            if project_run_id:
                self.project_run_submitted.emit(project_run_id, {"action": "refresh"})
            return
        self.project_run_submitted.emit(payload.get("project_run_id"), {**payload, "action": action})
