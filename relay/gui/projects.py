"""Phase 4 registered-Projects GUI widgets."""

from __future__ import annotations

import json
import os
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


def _format_json(value):
    return f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))}</pre>"


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
    decoded.setdefault("name", project.get("name", ""))
    return decoded
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
        header.addWidget(QLabel("<b>Projects</b>"), 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self.refresh_button)
        self.create_button = QPushButton("New Project")
        self.create_button.clicked.connect(self.create_project_requested.emit)
        header.addWidget(self.create_button)
        layout.addLayout(header)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #475569; font-size: 11px;")
        header.addWidget(self.count_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by name")
        self.search_edit.textChanged.connect(self._rerender)
        layout.addWidget(self.search_edit)
        self.list_widget = QListWidget()
        self.list_widget.itemActivated.connect(self._item_activated)
        layout.addWidget(self.list_widget, 1)

    def set_projects(self, projects):
        self.projects = list(projects)
        self.projects_by_id = {str(p.get("project_id")): p for p in projects if p.get("project_id")}
        self._rerender()

    def selected_project_id(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _rerender(self):
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
            self.select_project_requested.emit(str(project_id))


class ProjectDetailView(QWidget):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    run_requested = Signal(str)
    refresh_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project_id = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Project")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(self.title_label, 1)
        self.status_label = QLabel("")
        header.addWidget(self.status_label)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._on_refresh)
        header.addWidget(self.refresh_button)
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self._on_run)
        header.addWidget(self.run_button)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._on_edit)
        header.addWidget(self.edit_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._on_delete)
        header.addWidget(self.delete_button)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.overview_browser = QTextBrowser()
        self.definition_browser = QTextBrowser()
        self.runs_browser = QTextBrowser()
        self.tabs.addTab(self.overview_browser, "Overview")
        self.tabs.addTab(self.definition_browser, "Definition")
        self.tabs.addTab(self.runs_browser, "Runs")
        layout.addWidget(self.tabs, 1)
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_project(self, project, runs=None):
        self.project_id = str(project.get("project_id") or "") or None
        self.title_label.setText(str(project.get("name") or self.project_id or "Project"))
        version = int(project.get("version") or 1)
        deleted_at = project.get("deleted_at")
        status = "Deleted" if deleted_at else "Active"
        self.status_label.setText(f"v{version} · {status}")
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(self.project_id is not None and not deleted_at)
        self.definition_browser.setHtml(_format_json(_definition_from_project(project)))
        self.overview_browser.setHtml(
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
            )
        )
        self.runs_browser.setHtml(self._format_runs(runs or []))

    def clear(self):
        self.project_id = None
        self.title_label.setText("Project")
        self.status_label.setText("")
        for browser in (self.overview_browser, self.definition_browser, self.runs_browser):
            browser.clear()
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_runs(self, runs):
        self.runs_browser.setHtml(self._format_runs(runs))

    @staticmethod
    def _format_runs(runs):
        if not runs:
            return "<i>No Project Runs recorded for this Project yet.</i>"
        rows = ""
        for run in runs:
            rows += (
                "<tr>"
                f"<td>{escape(str(run.get('project_run_id') or '—'))}</td>"
                f"<td>{escape(str(run.get('status') or '—'))}</td>"
                f"<td>{escape(str(run.get('created_at') or '—'))}</td>"
                f"<td>{escape(str(run.get('trigger_type') or '—'))}</td>"
                "</tr>"
            )
        return f"<table><tr><th>Run</th><th>Status</th><th>Created</th><th>Trigger</th></tr>{rows}</table>"

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

    def _on_delete(self):
        if self.project_id:
            self.delete_requested.emit(self.project_id)

    def _on_delete(self):
        if self.project_id:
            self.delete_requested.emit(self.project_id)


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
        self.resize(820, 640)
        self._task_choices = [
            f"{task.get('name')} ({task.get('task_id')})" for task in (available_tasks or []) if task.get("task_id")
        ]
        self._task_id_by_label = {
            f"{task.get('name')} ({task.get('task_id')})": str(task.get("task_id"))
            for task in (available_tasks or [])
            if task.get("task_id")
        }
        self._delivery_roots = delivery_roots or []
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Project name", self.name_edit)
        self.description_edit = QLineEdit()
        form.addRow("Description", self.description_edit)
        root.addLayout(form)
        root.addWidget(QLabel("<b>Nodes</b>"))
        self.nodes_table = QTableWidget(0, 3)
        self.nodes_table.setHorizontalHeaderLabels(["Node ID", "Task", "Checkpoint (JSON)"])
        self.nodes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.nodes_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.nodes_table, 2)
        node_buttons = QHBoxLayout()
        self.add_node_button = QPushButton("Add node")
        self.add_node_button.clicked.connect(self._on_add_node)
        self.remove_node_button = QPushButton("Remove node")
        self.remove_node_button.clicked.connect(self._on_remove_node)
        node_buttons.addWidget(self.add_node_button)
        node_buttons.addWidget(self.remove_node_button)
        node_buttons.addStretch(1)
        root.addLayout(node_buttons)
        root.addWidget(QLabel("<b>Connections</b> (from node, role -> to node, alias A1/A2/...)"))
        self.connections_table = QTableWidget(0, 4)
        self.connections_table.setHorizontalHeaderLabels(["From node", "Role", "To node", "Alias"])
        self.connections_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.connections_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.connections_table, 2)
        conn_buttons = QHBoxLayout()
        self.add_conn_button = QPushButton("Add connection")
        self.add_conn_button.clicked.connect(self._on_add_connection)
        self.remove_conn_button = QPushButton("Remove connection")
        self.remove_conn_button.clicked.connect(self._on_remove_connection)
        conn_buttons.addWidget(self.add_conn_button)
        conn_buttons.addWidget(self.remove_conn_button)
        conn_buttons.addStretch(1)
        root.addLayout(conn_buttons)
        root.addWidget(QLabel("<b>Final outputs</b> (node_id, role)"))
        self.outputs_table = QTableWidget(0, 2)
        self.outputs_table.setHorizontalHeaderLabels(["Node", "Role"])
        self.outputs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.outputs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.outputs_table, 1)
        output_buttons = QHBoxLayout()
        self.add_output_button = QPushButton("Add output")
        self.add_output_button.clicked.connect(self._on_add_output)
        self.remove_output_button = QPushButton("Remove output")
        self.remove_output_button.clicked.connect(self._on_remove_output)
        output_buttons.addWidget(self.add_output_button)
        output_buttons.addWidget(self.remove_output_button)
        output_buttons.addStretch(1)
        root.addLayout(output_buttons)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #991B1B;")
        root.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if project:
            self._populate(project)

    def show_error(self, message):
        self.error_label.setText(message)

    def _populate(self, project):
        self.name_edit.setText(str(project.get("name") or ""))
        self.description_edit.setText(str(project.get("description") or ""))
        definition = _definition_from_project(project)
        self._populate_nodes(definition.get("nodes") or [])
        self._populate_connections(definition.get("connections") or [])
        self._populate_outputs(definition.get("output_selection") or [])

    def _populate_nodes(self, nodes):
        self.nodes_table.setRowCount(0)
        for node in nodes:
            row = self.nodes_table.rowCount()
            self.nodes_table.insertRow(row)
            self._set_cell(self.nodes_table, row, 0, str(node.get("node_id") or ""))
            label = self._label_for_task_id(str(node.get("task_id") or ""))
            self._set_cell(self.nodes_table, row, 1, label)
            checkpoint = node.get("checkpoint") or {}
            if isinstance(checkpoint, dict) and checkpoint:
                self._set_cell(self.nodes_table, row, 2, json.dumps(checkpoint))
            else:
                self._set_cell(self.nodes_table, row, 2, "")

    def _populate_connections(self, connections):
        self.connections_table.setRowCount(0)
        for connection in connections:
            row = self.connections_table.rowCount()
            self.connections_table.insertRow(row)
            self._set_cell(self.connections_table, row, 0, str(connection.get("from_node") or ""))
            self._set_cell(self.connections_table, row, 1, str(connection.get("from_role") or ""))
            self._set_cell(self.connections_table, row, 2, str(connection.get("to_node") or ""))
            self._set_cell(self.connections_table, row, 3, str(connection.get("to_alias") or ""))

    def _populate_outputs(self, outputs):
        self.outputs_table.setRowCount(0)
        for output in outputs:
            row = self.outputs_table.rowCount()
            self.outputs_table.insertRow(row)
            self._set_cell(self.outputs_table, row, 0, str(output.get("node_id") or ""))
            self._set_cell(self.outputs_table, row, 1, str(output.get("role") or ""))

    def _label_for_task_id(self, task_id):
        for label, value in self._task_id_by_label.items():
            if value == task_id:
                return label
        return task_id

    @staticmethod
    def _set_cell(table, row, column, text):
        item = QTableWidgetItem(text)
        table.setItem(row, column, item)

    def _on_add_node(self):
        row = self.nodes_table.rowCount()
        self.nodes_table.insertRow(row)
        self._set_cell(self.nodes_table, row, 0, "")
        self._set_cell(self.nodes_table, row, 1, self._task_choices[0] if self._task_choices else "")
        self._set_cell(self.nodes_table, row, 2, "")

    def _on_remove_node(self):
        rows = sorted({item.row() for item in self.nodes_table.selectedIndexes()}, reverse=True)
        for index in rows:
            self.nodes_table.removeRow(index)

    def _on_add_connection(self):
        row = self.connections_table.rowCount()
        self.connections_table.insertRow(row)
        for column in range(4):
            self._set_cell(self.connections_table, row, column, "")

    def _on_remove_connection(self):
        rows = sorted({item.row() for item in self.connections_table.selectedIndexes()}, reverse=True)
        for index in rows:
            self.connections_table.removeRow(index)

    def _on_add_output(self):
        row = self.outputs_table.rowCount()
        self.outputs_table.insertRow(row)
        self._set_cell(self.outputs_table, row, 0, "")
        self._set_cell(self.outputs_table, row, 1, "")

    def _on_remove_output(self):
        rows = sorted({item.row() for item in self.outputs_table.selectedIndexes()}, reverse=True)
        for index in rows:
            self.outputs_table.removeRow(index)

    def _on_save(self):
        try:
            payload = self.payload()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.accepted_payload.emit(payload)
        self.accept()

    def payload(self):
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Project name is required.")
        nodes = []
        for row in range(self.nodes_table.rowCount()):
            node_id = self._row_text(self.nodes_table, row, 0)
            task_label = self._row_text(self.nodes_table, row, 1)
            checkpoint_text = self._row_text(self.nodes_table, row, 2)
            if not node_id:
                raise ValueError(f"Node {row + 1} has an empty node_id.")
            if not task_label:
                raise ValueError(f"Node {row + 1} must select a Task.")
            task_id = self._task_id_by_label.get(task_label, task_label)
            node = {"node_id": node_id, "task_id": task_id}
            checkpoint = self._parse_checkpoint(checkpoint_text)
            if checkpoint:
                node["checkpoint"] = checkpoint
            nodes.append(node)
        if not nodes:
            raise ValueError("A Project must declare at least one node.")
        connections = []
        for row in range(self.connections_table.rowCount()):
            from_node = self._row_text(self.connections_table, row, 0)
            from_role = self._row_text(self.connections_table, row, 1)
            to_node = self._row_text(self.connections_table, row, 2)
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
            node_id = self._row_text(self.outputs_table, row, 0)
            role = self._row_text(self.outputs_table, row, 1)
            if not (node_id and role):
                continue
            output_selection.append({"node_id": node_id, "role": role})
        return {
            "name": name,
            "description": self.description_edit.text().strip() or None,
            "failure_policy": "stop",
            "nodes": nodes,
            "connections": connections,
            "output_selection": output_selection,
        }

    @staticmethod
    def _row_text(table, row, column):
        item = table.item(row, column)
        return item.text().strip() if item else ""

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
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(
            lambda: self.accepted_action.emit("refresh", {"project_run_id": self.project_run_id})
        )
        action_row.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("Cancel run")
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
            self.steps_browser.setHtml("<i>No step telemetry yet for this Project Run.</i>")
            return
        rows = ""
        for step in ordered:
            rows += (
                "<tr>"
                f"<td>{escape(str(step.get('node_id') or '-'))}</td>"
                f"<td>{escape(str(step.get('task_id') or '-'))}</td>"
                f"<td>{escape(str(step.get('status') or '-'))}</td>"
                f"<td>{escape(str(step.get('error_code') or '-'))}</td>"
                "</tr>"
            )
        self.steps_browser.setHtml(
            f"<table><tr><th>Node</th><th>Task</th><th>Status</th><th>Error</th></tr>{rows}</table>"
        )

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
        root = QVBoxLayout(self)
        root.addWidget(QLabel("<h2>Projects</h2>"))
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
        self.editor.open()

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
