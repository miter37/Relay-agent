"""Phase 3 registered-Tasks GUI widgets.

The Task widgets only render state and emit signals. ``MainWindow`` owns
request dispatch and response correlation through ``GuiRpcClient``. All
destructive actions require explicit confirmation by the caller; the
widgets never delete or rerun a Task silently.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..task_inputs import compile_definitions, extract_definitions, normalize_definition, validate_inputs
from .design_html import kv_row, td, th_row
from .design_typography import apply_type
from .design_widgets import IconButton, LabeledButton
from .scroll_state import preserve_scroll, set_html, set_plain_text

_WORKER_CHOICES: tuple[str, ...] = ("auto", "claude", "codex", "antigravity")
_RESULT_FORMATS: tuple[str, ...] = ("json", "txt")
_PROFILE_CHOICES: tuple[str, ...] = (
    "evidence-research",
    "decision-brief",
    "data-validation",
    "analysis-only",
    "artifact-production",
    "code-review",
)


class InputDefinitionDialog(QDialog):
    def __init__(self, definition=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit input item" if definition else "Add input item")
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Text", "Number", "Yes/No", "Choice"])
        self.shape_combo = QComboBox()
        self.shape_combo.addItems(["Single value", "List"])
        self.required = QCheckBox("Required")
        self.description = QTextEdit()
        self.description.setMinimumHeight(55)
        self.choices = QTextEdit()
        self.choices.setPlaceholderText("One allowed value per line")
        self.choices.setMinimumHeight(55)
        self.has_default = QCheckBox("Use a default value")
        self.default = QTextEdit()
        self.default.setPlaceholderText("One item per line for lists")
        self.default.setMinimumHeight(55)
        form.addRow("Item name", self.name_edit)
        form.addRow("Value type", self.type_combo)
        form.addRow("Shape", self.shape_combo)
        form.addRow("", self.required)
        form.addRow("Description", self.description)
        form.addRow("Allowed values", self.choices)
        form.addRow("", self.has_default)
        form.addRow("Default", self.default)
        root.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.type_combo.currentTextChanged.connect(self._update_visibility)
        self.has_default.toggled.connect(self.default.setEnabled)
        if definition:
            self._populate(definition)
        self._update_visibility()

    def _update_visibility(self) -> None:
        self.choices.setVisible(self.type_combo.currentText() == "Choice")
        self.default.setEnabled(self.has_default.isChecked())

    def _populate(self, value: dict) -> None:
        self.name_edit.setText(value.get("name", ""))
        self.type_combo.setCurrentText(
            {"text": "Text", "number": "Number", "boolean": "Yes/No", "choice": "Choice"}[
                value.get("value_type", "text")
            ]
        )
        self.shape_combo.setCurrentText("List" if value.get("cardinality") == "list" else "Single value")
        self.required.setChecked(bool(value.get("required")))
        self.description.setPlainText(value.get("description", ""))
        self.choices.setPlainText("\n".join(value.get("choices") or []))
        self.has_default.setChecked(bool(value.get("has_default")))
        default = value.get("default")
        self.default.setPlainText(
            "\n".join(map(str, default)) if isinstance(default, list) else "" if default is None else str(default)
        )

    def value(self) -> dict:
        value_type = {"Text": "text", "Number": "number", "Yes/No": "boolean", "Choice": "choice"}[
            self.type_combo.currentText()
        ]
        cardinality = "list" if self.shape_combo.currentText() == "List" else "single"
        raw = self.default.toPlainText().strip()
        default = None
        if self.has_default.isChecked():
            if cardinality == "list":
                default = [self._coerce_default(line.strip(), value_type) for line in raw.splitlines() if line.strip()]
            else:
                default = self._coerce_default(raw, value_type)
        return normalize_definition(
            {
                "name": self.name_edit.text(),
                "description": self.description.toPlainText(),
                "value_type": value_type,
                "cardinality": cardinality,
                "required": self.required.isChecked(),
                "choices": [line.strip() for line in self.choices.toPlainText().splitlines()],
                "has_default": self.has_default.isChecked(),
                "default": default,
            }
        )

    @staticmethod
    def _coerce_default(raw: str, value_type: str):
        if value_type == "number":
            try:
                return float(raw)
            except ValueError as exc:
                raise ValueError("Number default must be a number.") from exc
        if value_type == "boolean":
            if raw.casefold() not in {"true", "false"}:
                raise ValueError("Yes/No default must be true or false.")
            return raw.casefold() == "true"
        return raw

    def _accept(self) -> None:
        try:
            self.value()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()


class InputDefinitionsEditor(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.definitions: list[dict] = []
        self.advanced_schema: str | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Task inputs</b>"))
        self.info = QLabel("Define the values a person supplies each time this Task runs.")
        self.info.setObjectName("mutedText")
        apply_type(self.info, "caption")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.list = QListWidget()
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.add_button = IconButton("plus", "Add an input")
        self.edit_button = IconButton("pencil", "Edit this input")
        self.delete_button = IconButton("trash", "Delete this input", tone="danger")
        self.up_button = IconButton("arrow-up", "Move this input up")
        self.down_button = IconButton("arrow-down", "Move this input down")
        for button in (self.add_button, self.edit_button, self.delete_button, self.up_button, self.down_button):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))

    def set_schema(self, schema) -> None:
        definitions = extract_definitions(schema)
        self.definitions = definitions or []
        self.advanced_schema = str(schema) if definitions is None else None
        for button in (self.add_button, self.edit_button, self.delete_button, self.up_button, self.down_button):
            button.setEnabled(definitions is not None)
        self.info.setText(
            "This Task has an advanced CLI/Agent schema. GUI editing is unavailable; the schema is preserved."
            if definitions is None
            else "Define the values a person supplies each time this Task runs."
        )
        self._render()

    def schema(self) -> str | None:
        if self.advanced_schema is not None:
            return self.advanced_schema
        return json.dumps(compile_definitions(self.definitions), ensure_ascii=False) if self.definitions else None

    def _render(self) -> None:
        with preserve_scroll(self.list):
            self.list.clear()
            for item in self.definitions:
                self.list.addItem(
                    f"{item['name']} · {item['value_type']} · {item['cardinality']} · {'required' if item['required'] else 'optional'}"
                )

    def _add(self) -> None:
        dialog = InputDefinitionDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.definitions.append(dialog.value())
            self._render()

    def _edit(self) -> None:
        index = self.list.currentRow()
        if index < 0:
            return
        dialog = InputDefinitionDialog(self.definitions[index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.definitions[index] = dialog.value()
            self._render()

    def _delete(self) -> None:
        index = self.list.currentRow()
        if index >= 0:
            self.definitions.pop(index)
            self._render()

    def _move(self, direction: int) -> None:
        index = self.list.currentRow()
        target = index + direction
        if index < 0 or target < 0 or target >= len(self.definitions):
            return
        self.definitions[index], self.definitions[target] = self.definitions[target], self.definitions[index]
        self._render()
        self.list.setCurrentRow(target)


class PortDefinitionDialog(QDialog):
    """Small, human-readable editor for one Artifact input or Output port."""

    def __init__(self, *, output: bool, definition: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Output" if output and definition else "Add Output" if output else "Edit Artifact input" if definition else "Add Artifact input")
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("report" if output else "research")
        form.addRow("Output role" if output else "Input name", self.name_edit)
        self.format_edit = QLineEdit()
        self.format_edit.setPlaceholderText("Any file, or text/html, application/json")
        form.addRow("Produces" if output else "Accepts", self.format_edit)
        self.required = QCheckBox("Required")
        form.addRow("", self.required)
        self.cardinality = QComboBox()
        self.cardinality.addItem("One", "one")
        self.cardinality.addItem("Many", "many")
        form.addRow("Cardinality", self.cardinality)
        self.description = QLineEdit()
        self.description.setPlaceholderText("Optional short description")
        form.addRow("Description", self.description)
        root.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if definition:
            self.name_edit.setText(str(definition.get("role" if output else "name") or ""))
            formats = definition.get("produces" if output else "accepts") or []
            self.format_edit.setText(", ".join(formats if isinstance(formats, list) else [str(formats)]))
            self.required.setChecked(bool(definition.get("required")))
            self.cardinality.setCurrentText("Many" if definition.get("cardinality") == "many" else "One")
            self.description.setText(str(definition.get("description") or ""))

    def value(self) -> dict:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("A name is required.")
        formats = [item.strip() for item in self.format_edit.text().split(",") if item.strip()]
        result = {
            "role" if self._is_output() else "name": name,
            "required": self.required.isChecked(),
            "cardinality": self.cardinality.currentData() or "one",
        }
        if formats:
            result["produces" if self._is_output() else "accepts"] = formats
        if self.description.text().strip():
            result["description"] = self.description.text().strip()
        return result

    def _is_output(self) -> bool:
        return "Output" in self.windowTitle() and "input" not in self.windowTitle()

    def _accept(self) -> None:
        try:
            self.value()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()


class InterfacePortsEditor(QWidget):
    """Row editor shared by the Artifact inputs and Outputs sections."""

    def __init__(self, *, output: bool, parent=None) -> None:
        super().__init__(parent)
        self.output = output
        self.ports: list[dict] = []
        layout = QVBoxLayout(self)
        self.info = QLabel(
            "Relay always provides the primary result Output. Add named Outputs when a Project should select them."
            if output
            else "Results from another Task or Run arrive here by name; Parameters are separate values."
        )
        self.info.setObjectName("mutedText")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.list = QListWidget()
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        self.add_button = IconButton("plus", "Add an Output" if output else "Add an Artifact input")
        self.edit_button = IconButton("pencil", "Edit selected interface port")
        self.delete_button = IconButton("trash", "Delete selected interface port", tone="danger")
        for button in (self.add_button, self.edit_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)

    def set_ports(self, ports) -> None:
        self.ports = [dict(item) for item in ports or [] if isinstance(item, dict)]
        self._render()

    def _render(self) -> None:
        with preserve_scroll(self.list):
            self.list.clear()
            for port in self.ports:
                key = "role" if self.output else "name"
                formats = port.get("produces" if self.output else "accepts") or ["Any file"]
                required = "required" if port.get("required") else "optional"
                many = " · many" if port.get("cardinality") == "many" else ""
                self.list.addItem(f"{port.get(key) or 'Unnamed'} · {', '.join(formats)} · {required}{many}")

    def _add(self) -> None:
        dialog = PortDefinitionDialog(output=self.output, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.ports.append(dialog.value())
            self._render()

    def _edit(self) -> None:
        index = self.list.currentRow()
        if index < 0:
            return
        dialog = PortDefinitionDialog(output=self.output, definition=self.ports[index], parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.ports[index] = dialog.value()
            self._render()

    def _delete(self) -> None:
        index = self.list.currentRow()
        if index >= 0:
            self.ports.pop(index)
            self._render()


def _format_fields(payload: dict) -> str:
    if not payload:
        return "<i>No details available.</i>"
    rows = "".join(kv_row(key, value or "—") for key, value in payload.items())
    return f"<table>{rows}</table>"


def _task_status_label(task: dict) -> str:
    version = task.get("version") or 1
    return f"v{int(version)} · {task.get('default_worker') or 'auto'}"


class TaskListView(QWidget):
    select_task_requested = Signal(str)
    create_task_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tasks: list[dict] = []
        self.tasks_by_id: dict[str, dict] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel("Registered Tasks")
        title.setObjectName("sectionTitle")
        apply_type(title, "title.section")
        header.addWidget(title, 1)
        self.refresh_button = IconButton("refresh", "Refresh the Task list")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self.refresh_button)
        self.create_button = IconButton("plus", "Register a new Task", tone="accent")
        self.create_button.clicked.connect(self.create_task_requested.emit)
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
        self.empty_label = QLabel("No registered Tasks yet. Create a Task to begin.")
        self.empty_label.setObjectName("emptyHint")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        # Same stretch as the list it stands in for, so exactly one of the two
        # fills the column instead of both sharing it.
        layout.addWidget(self.empty_label, 1)
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._item_changed)
        layout.addWidget(self.list_widget, 1)

    def set_tasks(self, tasks: list[dict]) -> None:
        self.tasks = list(tasks)
        self.tasks_by_id = {str(t.get("task_id")): t for t in tasks if t.get("task_id")}
        self._rerender()

    def selected_task_id(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _rerender(self) -> None:
        with preserve_scroll(self.list_widget):
            self._rerender_content()

    def _rerender_content(self) -> None:
        query = self.search_edit.text().strip().casefold()
        self.list_widget.clear()
        visible = 0
        for task in sorted(self.tasks, key=lambda row: str(row.get("name") or "").casefold()):
            name = str(task.get("name") or task.get("task_id") or "Task")
            if query and query not in name.casefold():
                continue
            item = QListWidgetItem(f"{name} · {_task_status_label(task)}")
            item.setData(Qt.UserRole, str(task.get("task_id") or ""))
            self.list_widget.addItem(item)
            visible += 1
        total = len(self.tasks)
        self.empty_label.setText(
            "No Tasks match this filter."
            if total and query and not visible
            else "No registered Tasks yet. Create a Task to begin."
        )
        self.empty_label.setVisible(not visible)
        # The empty hint replaces the list rather than stacking a second empty box
        # under it.
        self.list_widget.setVisible(bool(visible))
        if not total:
            self.count_label.setText("No registered Tasks")
        elif query and visible != total:
            self.count_label.setText(f"{visible} of {total} tasks match")
        elif query:
            self.count_label.setText(f"{total} tasks match")
        elif total >= 200:
            self.count_label.setText(f"{total} tasks (server may have more)")
        else:
            self.count_label.setText(f"{total} tasks")

    def _item_activated(self, item: QListWidgetItem) -> None:
        task_id = item.data(Qt.UserRole)
        if task_id:
            self.select_task_requested.emit(str(task_id))

    def _item_changed(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if item is not None:
            self._item_activated(item)


class TaskDetailView(QWidget):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    run_requested = Signal(str)
    refresh_requested = Signal(str)
    run_link_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = None

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Task")
        self.title_label.setObjectName("pageTitle")
        apply_type(self.title_label, "title.detail")
        header.addWidget(self.title_label, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("mutedText")
        apply_type(self.status_label, "caption")
        header.addWidget(self.status_label)
        self.refresh_button = IconButton("refresh", "Refresh this Task")
        self.refresh_button.clicked.connect(self._on_refresh)
        header.addWidget(self.refresh_button)
        self.edit_button = IconButton("pencil", "Edit this Task")
        self.edit_button.clicked.connect(self._on_edit)
        header.addWidget(self.edit_button)
        self.delete_button = IconButton("trash", "Delete this Task", tone="danger")
        self.delete_button.clicked.connect(self._on_delete)
        header.addWidget(self.delete_button)
        # Run is the one primary action on this screen, matching the same
        # promotion made on the Project detail screen: it should visibly
        # outweigh the quiet refresh/edit/delete icon row, not blend into it.
        self.run_button = LabeledButton("play", "Run", tone="primary")
        self.run_button.clicked.connect(self._on_run)
        header.addWidget(self.run_button)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.overview_browser = QTextBrowser()
        self.instructions_browser = QTextBrowser()
        self.policy_browser = QTextBrowser()
        self.run_browser = QTextBrowser()
        self.run_browser.setOpenLinks(False)
        self.run_browser.anchorClicked.connect(self._on_run_link)
        self.tabs.addTab(self.overview_browser, "Overview")
        self.tabs.addTab(self.instructions_browser, "Instructions")
        self.tabs.addTab(self.policy_browser, "Policies")
        self.tabs.addTab(self.run_browser, "Runs")
        layout.addWidget(self.tabs, 1)

        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_task(self, task: dict, runs=None) -> None:
        self.task_id = str(task.get("task_id") or "") or None
        self.title_label.setText(str(task.get("name") or self.task_id or "Task"))
        self.status_label.setText(f"v{int(task.get('version') or 1)} · {task.get('default_worker') or 'auto'}")
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(self.task_id is not None)
        set_html(
            self.overview_browser,
            _format_fields(
                {
                    "Task ID": task.get("task_id"),
                    "Name": task.get("name"),
                    "Description": task.get("description"),
                    "Version": task.get("version"),
                    "Default Worker": task.get("default_worker"),
                    "Fallback enabled": task.get("fallback_enabled"),
                    "Profile": task.get("profile"),
                    "Result format": task.get("result_format"),
                    "Timeout (s)": task.get("timeout_seconds"),
                    "Updated": task.get("updated_at"),
                    "Created": task.get("created_at"),
                }
            ),
        )
        set_plain_text(self.instructions_browser, str(task.get("instructions") or ""))
        set_html(
            self.policy_browser,
            _format_fields(
                {
                    "Input schema": task.get("input_schema"),
                    "Output contract": task.get("output_contract"),
                    "Validation policy": task.get("validation_policy"),
                }
            ),
        )
        set_html(self.run_browser, self._format_runs(runs or []))

    def clear(self) -> None:
        self.task_id = None
        self.title_label.setText("Task")
        self.status_label.setText("")
        set_html(self.overview_browser, "<p>No Task selected. Choose a Task from the list.</p>")
        with preserve_scroll(self.instructions_browser):
            self.instructions_browser.clear()
        with preserve_scroll(self.policy_browser):
            self.policy_browser.clear()
        set_html(self.run_browser, "<p>No Runs are available until a Task is selected.</p>")
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_runs(self, runs) -> None:
        set_html(self.run_browser, self._format_runs(runs))

    @staticmethod
    def _format_runs(runs) -> str:
        if not runs:
            return "<i>No Runs recorded for this Task yet.</i>"
        rows = "".join(
            f"<tr>{td(TaskDetailView._run_link(run))}"
            f"{td(run.get('status') or '—')}{td(run.get('completed_at') or run.get('created_at') or '—')}"
            f"{td(run.get('actual_worker') or run.get('requested_worker') or '—')}</tr>"
            for run in runs
        )
        return f"<table>{th_row(['Run', 'Status', 'When', 'Worker'])}{rows}</table>"

    @staticmethod
    def _run_link(run) -> str:
        run_id = str(run.get("task_run_id") or run.get("job_id") or run.get("run_id") or "—")
        if run_id == "—":
            return escape(run_id)
        return f'<a href="relay://task-run/{escape(run_id)}">{escape(run_id)}</a>'

    def _on_run_link(self, url) -> None:
        if url.scheme() == "relay" and url.host() == "task-run":
            run_id = url.path().lstrip("/")
            if run_id:
                self.run_link_requested.emit(run_id)

    def _on_refresh(self) -> None:
        if self.task_id:
            self.refresh_requested.emit(self.task_id)

    def _on_run(self) -> None:
        if self.task_id:
            self.run_requested.emit(self.task_id)

    def _on_edit(self) -> None:
        if self.task_id:
            self.edit_requested.emit(self.task_id)

    def _on_delete(self) -> None:
        if self.task_id:
            self.delete_requested.emit(self.task_id)


class TaskEditorDialog(QDialog):
    accepted_payload = Signal(dict)

    def __init__(self, *, task=None, available_workers=None, profiles=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Task" if task else "Register Task")
        # Wide, not tall: Instructions routinely holds long prompt text, and it's
        # easier to write/scan that with more horizontal room than more vertical
        # room. The short scalar fields below are split into two columns instead
        # of one long stack so Instructions isn't left with whatever is left over.
        self.resize(920, 720)
        self._task_id = str(task.get("task_id") or "") if task else ""
        self._saving = False

        root = QVBoxLayout(self)
        fields_row = QHBoxLayout()
        left_form = QFormLayout()
        right_form = QFormLayout()

        self.name_edit = QLineEdit()
        left_form.addRow("Name", self.name_edit)
        self.description_edit = QLineEdit()
        left_form.addRow("Description", self.description_edit)

        self.worker_combo = QComboBox()
        workers = list(_WORKER_CHOICES)
        for worker in available_workers or ():
            if worker and worker not in workers:
                workers.append(worker)
        self.worker_combo.addItems(workers)
        left_form.addRow("Default Worker", self.worker_combo)

        self.fallback_checkbox = QCheckBox("Allow Worker fallback")
        self.fallback_checkbox.setChecked(True)
        left_form.addRow("Fallback", self.fallback_checkbox)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 24 * 60 * 60)
        self.timeout_spin.setSpecialValueText("No timeout")
        self.timeout_spin.setValue(0)
        left_form.addRow("Timeout (seconds)", self.timeout_spin)

        self.profile_combo = QComboBox()
        self.profile_combo.addItems(list(profiles or _PROFILE_CHOICES))
        right_form.addRow("Profile", self.profile_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems(_RESULT_FORMATS)
        right_form.addRow("Result format", self.format_combo)

        self.validation_policy_edit = QLineEdit()
        self.validation_policy_edit.setPlaceholderText("Optional identifier, e.g. strict, lenient")
        right_form.addRow("Validation policy", self.validation_policy_edit)

        self.output_contract_edit = QTextEdit()
        self.output_contract_edit.setPlaceholderText("Legacy or advanced JSON; normal editing uses the Interface rows below")
        self.output_contract_edit.setAcceptRichText(False)
        self.output_contract_edit.setMinimumHeight(65)
        self.output_contract_edit.setVisible(False)

        fields_row.addLayout(left_form, 1)
        fields_row.addLayout(right_form, 1)
        root.addLayout(fields_row)

        self.input_definitions = InputDefinitionsEditor()
        root.addWidget(self.input_definitions)

        interface_box = QGroupBox("Task Interface · Artifact inputs & Outputs")
        interface_layout = QVBoxLayout(interface_box)
        self.artifact_inputs_editor = InterfacePortsEditor(output=False)
        self.outputs_editor = InterfacePortsEditor(output=True)
        interface_layout.addWidget(QLabel("<b>Artifact inputs</b>"))
        interface_layout.addWidget(self.artifact_inputs_editor)
        interface_layout.addWidget(QLabel("<b>Outputs</b>"))
        interface_layout.addWidget(self.outputs_editor)
        self.interface_declared_checkbox = QCheckBox("Declare this Interface even if it only uses Relay's result Output")
        self.interface_declared_checkbox.setToolTip(
            "Leave this off for a legacy Task. Turning it on lets Projects validate named inputs and Outputs before saving."
        )
        interface_layout.addWidget(self.interface_declared_checkbox)
        self.advanced_interface_checkbox = QCheckBox("Advanced / legacy JSON")
        interface_layout.addWidget(self.advanced_interface_checkbox)
        interface_layout.addWidget(self.output_contract_edit)
        self.advanced_interface_checkbox.toggled.connect(self.output_contract_edit.setVisible)
        root.addWidget(interface_box)

        review_box = QFormLayout()
        self.review_enabled_checkbox = QCheckBox("Require result review before publishing")
        self.review_enabled_checkbox.setToolTip(
            "The Task finishes first; its result becomes visible to the reviewer and is published only after confirmation."
        )
        review_box.addRow("Review gate", self.review_enabled_checkbox)
        self.review_reviewer_combo = QComboBox()
        self.review_reviewer_combo.addItem("Human", "human")
        review_box.addRow("Reviewer", self.review_reviewer_combo)
        self.review_guidelines_edit = QTextEdit()
        self.review_guidelines_edit.setAcceptRichText(False)
        self.review_guidelines_edit.setPlaceholderText("Optional notes about what the reviewer should check")
        self.review_guidelines_edit.setMaximumHeight(72)
        review_box.addRow("Review notes", self.review_guidelines_edit)
        self.review_max_reruns_spin = QSpinBox()
        self.review_max_reruns_spin.setRange(0, 20)
        self.review_max_reruns_spin.setSpecialValueText("Human decides")
        review_box.addRow("Automatic reruns", self.review_max_reruns_spin)
        root.addLayout(review_box)

        root.addWidget(QLabel("<b>Instructions</b>"))
        self.instructions_edit = QTextEdit()
        self.instructions_edit.setAcceptRichText(False)
        self.instructions_edit.setMinimumHeight(260)
        root.addWidget(self.instructions_edit, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("errorText")
        root.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        self.buttons = buttons
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.cancel_button = buttons.button(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if task:
            self._populate(task)

    def set_saving(self, saving: bool) -> None:
        self._saving = saving
        self.save_button.setEnabled(not saving)
        self.save_button.setText("Saving..." if saving else "Save")
        self.cancel_button.setEnabled(not saving)

    def report_save_error(self, message: str) -> None:
        self.set_saving(False)
        self.show_error(message)

    def close_after_save(self) -> None:
        self.accept()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)

    def _populate(self, task: dict) -> None:
        self.name_edit.setText(str(task.get("name") or ""))
        self.description_edit.setText(str(task.get("description") or ""))
        worker = str(task.get("default_worker") or "auto")
        if worker not in _WORKER_CHOICES:
            self.worker_combo.addItem(worker)
        self.worker_combo.setCurrentText(worker)
        self.fallback_checkbox.setChecked(bool(task.get("fallback_enabled", True)))
        self.timeout_spin.setValue(int(task.get("timeout_seconds") or 0))
        profile = str(task.get("profile") or "web-research")
        if profile not in _PROFILE_CHOICES:
            self.profile_combo.addItem(profile)
        self.profile_combo.setCurrentText(profile)
        result_format = str(task.get("result_format") or "json")
        if result_format not in _RESULT_FORMATS:
            self.format_combo.addItem(result_format)
        self.format_combo.setCurrentText(result_format)
        self.input_definitions.set_schema(task.get("input_schema"))
        raw_contract = task.get("output_contract")
        self.output_contract_edit.setPlainText(str(raw_contract or ""))
        self.artifact_inputs_editor.set_ports([])
        self.outputs_editor.set_ports([])
        self.interface_declared_checkbox.setChecked(False)
        self.advanced_interface_checkbox.setChecked(False)
        try:
            contract = json.loads(raw_contract) if isinstance(raw_contract, str) and raw_contract else raw_contract
        except (TypeError, ValueError):
            contract = None
        if isinstance(contract, dict) and contract.get("interface_version") == 1:
            self.artifact_inputs_editor.set_ports(contract.get("artifact_inputs"))
            self.outputs_editor.set_ports(contract.get("outputs"))
            self.interface_declared_checkbox.setChecked(True)
        elif raw_contract:
            self.advanced_interface_checkbox.setChecked(True)
        self.validation_policy_edit.setText(str(task.get("validation_policy") or ""))
        self.instructions_edit.setPlainText(str(task.get("instructions") or ""))
        review = task.get("review_policy") or {}
        if isinstance(review, str):
            try:
                review = json.loads(review)
            except json.JSONDecodeError:
                review = {}
        self.review_enabled_checkbox.setChecked(bool(review.get("enabled")))
        self.review_guidelines_edit.setPlainText(str(review.get("guidelines") or ""))
        self.review_max_reruns_spin.setValue(int(review.get("max_reruns") or 0))

    def _on_save(self) -> None:
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

    def payload(self) -> dict:
        name = self.name_edit.text().strip()
        instructions = self.instructions_edit.toPlainText().strip()
        if not name:
            raise ValueError("Task name is required.")
        if not instructions:
            raise ValueError("Instructions are required.")
        payload: dict = {
            "name": name,
            "description": self.description_edit.text().strip() or None,
            "default_worker": self.worker_combo.currentText().strip() or "auto",
            "fallback_enabled": self.fallback_checkbox.isChecked(),
            "timeout_seconds": int(self.timeout_spin.value()) or None,
            "profile": self.profile_combo.currentText().strip() or None,
            "result_format": self.format_combo.currentText().strip() or "json",
            "instructions": instructions,
        }
        input_schema = self.input_definitions.schema()
        if input_schema:
            payload["input_schema"] = input_schema
        output_contract_text = self.output_contract_edit.toPlainText().strip()
        if self.interface_declared_checkbox.isChecked():
            from ..task_interface import normalize_interface

            try:
                payload["output_contract"] = json.dumps(
                    normalize_interface(
                        {
                            "interface_version": 1,
                            "artifact_inputs": self.artifact_inputs_editor.ports,
                            "outputs": self.outputs_editor.ports,
                        }
                    ),
                    ensure_ascii=False,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Task Interface is invalid: {exc}") from exc
        elif self.advanced_interface_checkbox.isChecked() and output_contract_text:
            try:
                json.loads(output_contract_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Advanced output contract is not valid JSON: {exc}") from exc
            payload["output_contract"] = output_contract_text
        validation = self.validation_policy_edit.text().strip()
        if validation:
            payload["validation_policy"] = validation
        if self.review_enabled_checkbox.isChecked():
            policy = {
                "enabled": True,
                "reviewer": "human",
                "max_reruns": int(self.review_max_reruns_spin.value()),
            }
            guidelines = self.review_guidelines_edit.toPlainText().strip()
            if guidelines:
                policy["guidelines"] = guidelines
            payload["review_policy"] = policy
        return payload

    @property
    def editing_task_id(self):
        return self._task_id or None


class TaskRunDialog(QDialog):
    accepted_overrides = Signal(dict)
    files_from_run_requested = Signal(str)

    def __init__(self, *, task: dict, available_workers=None, profiles=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Run Task · {task.get('name') or task.get('task_id') or 'Task'}")
        self.resize(560, 640)
        self._task = dict(task)
        self._advanced_input_schema = extract_definitions(task.get("input_schema")) is None
        self._input_fields: dict[str, tuple[QWidget, dict]] = {}
        self._artifact_inputs: list[dict] = []
        layout = QVBoxLayout(self)
        info = QLabel("Configure optional overrides. Leave fields blank to use the registered defaults.")
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.worker_combo = QComboBox()
        workers = ["auto"]
        for worker in available_workers or ():
            if worker and worker not in workers:
                workers.append(worker)
        self.worker_combo.addItems(workers)
        form.addRow("Worker override", self.worker_combo)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("(default)")
        self.profile_combo.addItems(list(profiles or _PROFILE_CHOICES))
        form.addRow("Profile override", self.profile_combo)
        self.format_combo = QComboBox()
        self.format_combo.addItem("(default)")
        self.format_combo.addItems(_RESULT_FORMATS)
        form.addRow("Result format override", self.format_combo)
        self.review_combo = QComboBox()
        self.review_combo.addItem("Use Task setting", "inherit")
        self.review_combo.addItem("Require human review", "human")
        self.review_combo.addItem("Skip review for this run", "off")
        form.addRow("Review gate", self.review_combo)
        layout.addLayout(form)

        self.inputs_form = QFormLayout()
        self._build_input_form(task.get("input_schema"))
        if self._input_fields:
            layout.addWidget(QLabel("<b>Task inputs</b>"))
            layout.addLayout(self.inputs_form)
        elif self._advanced_input_schema:
            warning = QLabel("This Task uses an advanced CLI/Agent input schema and cannot be safely run from the GUI.")
            warning.setObjectName("errorText")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        layout.addWidget(QLabel("<b>Input files</b>"))
        self.attachment_list = QListWidget()
        self.attachment_list.setMaximumHeight(90)
        layout.addWidget(self.attachment_list)
        attachment_actions = QHBoxLayout()
        add_files = LabeledButton("plus", "Add files")
        add_files.clicked.connect(self._choose_attachments)
        attachment_actions.addWidget(add_files)
        self.add_from_run_button = LabeledButton("plus", "Add from Task Run")
        self.add_from_run_button.clicked.connect(self._choose_source_run)
        attachment_actions.addWidget(self.add_from_run_button)
        attachment_actions.addStretch(1)
        layout.addLayout(attachment_actions)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        try:
            overrides = self.overrides()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            return
        self.accepted_overrides.emit(overrides)
        self.accept()

    def overrides(self) -> dict:
        if self._advanced_input_schema:
            raise ValueError("This Task's advanced input schema must be run through CLI or an Agent.")
        overrides: dict = {}
        worker = self.worker_combo.currentText()
        if worker and worker != "auto":
            overrides["worker"] = worker
        profile = self.profile_combo.currentText()
        if profile and profile != "(default)":
            overrides["profile"] = profile
        result_format = self.format_combo.currentText()
        if result_format and result_format != "(default)":
            overrides["format"] = result_format
        review_mode = self.review_combo.currentData()
        if review_mode and review_mode != "inherit":
            overrides["review_mode"] = review_mode
        inputs = self._input_values()
        if inputs:
            overrides["inputs"] = inputs
        attachments = [self.attachment_list.item(index).text() for index in range(self.attachment_list.count())]
        if attachments:
            overrides["attachments"] = attachments
        if self._artifact_inputs:
            overrides["artifact_inputs"] = list(self._artifact_inputs)
        return overrides

    @staticmethod
    def _parse_input_schema(value) -> dict:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Task input schema is not valid JSON: {exc}") from exc
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("Task input schema must be a JSON object.")
        return value

    def _build_input_form(self, value) -> None:
        if self._advanced_input_schema:
            return
        schema = self._parse_input_schema(value)
        required = {str(name) for name in schema.get("required") or []}
        for name, definition in (schema.get("properties") or {}).items():
            if not isinstance(definition, dict):
                continue
            field_name = str(name)
            widget = self._input_widget(definition)
            label = field_name + (" *" if field_name in required else "")
            description = str(definition.get("description") or "")
            if description:
                widget.setToolTip(description)
            self.inputs_form.addRow(label, widget)
            self._input_fields[field_name] = (widget, definition)

    @staticmethod
    def _input_widget(definition: dict) -> QWidget:
        field_type = definition.get("type", "string")
        if field_type == "array":
            widget = QTextEdit()
            widget.setAcceptRichText(False)
            widget.setMinimumHeight(70)
            widget.setPlaceholderText("One item per line")
            if isinstance(definition.get("default"), list):
                widget.setPlainText("\n".join(map(str, definition["default"])))
            return widget
        choices = definition.get("enum")
        if isinstance(choices, list) and choices:
            widget = QComboBox()
            widget.addItems([str(value) for value in choices])
            if definition.get("default") in choices:
                widget.setCurrentText(str(definition["default"]))
            return widget
        if field_type == "boolean":
            widget = QCheckBox()
            widget.setChecked(bool(definition.get("default", False)))
            return widget
        if field_type == "integer":
            widget = QSpinBox()
            widget.setRange(-1_000_000_000, 1_000_000_000)
            if definition.get("default") is not None:
                widget.setValue(int(definition["default"]))
            return widget
        if field_type == "object":
            widget = QTextEdit()
            widget.setAcceptRichText(False)
            widget.setMinimumHeight(70)
            if definition.get("default") is not None:
                widget.setPlainText(json.dumps(definition["default"], ensure_ascii=False))
            return widget
        widget = QLineEdit()
        if definition.get("default") is not None:
            default = definition["default"]
            widget.setText(
                ", ".join(map(str, default)) if field_type == "array" and isinstance(default, list) else str(default)
            )
        if field_type == "array":
            widget.setPlaceholderText("Comma-separated values")
        elif field_type == "number":
            widget.setPlaceholderText("Number")
        return widget

    def _input_values(self) -> dict:
        schema = self._parse_input_schema(self._task.get("input_schema"))
        values: dict = {}
        required = {str(name) for name in schema.get("required") or []}
        for name, (widget, definition) in self._input_fields.items():
            field_type = definition.get("type", "string")
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            elif isinstance(widget, QTextEdit):
                raw = widget.toPlainText().strip()
                if field_type == "array":
                    item_definition = definition.get("items") if isinstance(definition.get("items"), dict) else {}
                    value = (
                        [
                            self._coerce_input_value(name, line.strip(), item_definition)
                            for line in raw.splitlines()
                            if line.strip()
                        ]
                        if raw
                        else None
                    )
                elif not raw:
                    value = None
                else:
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Task input {name!r} must be valid JSON: {exc}") from exc
            elif isinstance(widget, QComboBox):
                value = widget.currentText()
            else:
                raw = widget.text().strip()
                if field_type == "array":
                    value = [item.strip() for item in raw.split(",") if item.strip()] if raw else None
                elif field_type == "number":
                    try:
                        value = float(raw) if raw else None
                    except ValueError as exc:
                        raise ValueError(f"Task input {name!r} must be a number.") from exc
                else:
                    value = raw or None
            if value is not None:
                values[name] = value
            elif name in required:
                raise ValueError(f"Task input {name!r} is required.")
        return validate_inputs(values, schema)

    @staticmethod
    def _coerce_input_value(name: str, raw: str, definition: dict):
        field_type = definition.get("type", "string")
        if field_type == "number":
            try:
                return float(raw)
            except ValueError as exc:
                raise ValueError(f"Task input {name!r} must contain numbers only.") from exc
        if field_type == "boolean":
            if raw.casefold() not in {"true", "false"}:
                raise ValueError(f"Task input {name!r} must use true or false, one item per line.")
            return raw.casefold() == "true"
        return raw

    @staticmethod
    def _validate_input_values(values: dict, schema: dict) -> None:
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            unknown = [name for name in values if name not in properties]
            if unknown:
                raise ValueError(f"Unknown Task inputs: {', '.join(unknown)}")
        checks = {
            "string": lambda value: isinstance(value, str),
            "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
            "boolean": lambda value: isinstance(value, bool),
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
            "null": lambda value: value is None,
        }
        for name, value in values.items():
            definition = properties.get(name) or {}
            expected = definition.get("type")
            check = checks.get(expected)
            if check and not check(value):
                raise ValueError(f"Task input {name!r} must be {expected}.")
            choices = definition.get("enum")
            if isinstance(choices, list) and value not in choices:
                raise ValueError(f"Task input {name!r} must be one of: {', '.join(map(str, choices))}.")

    def _choose_attachments(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add input files")
        self.add_attachments(paths)

    def _choose_source_run(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        run_id, accepted = QInputDialog.getText(self, "Add files from Task Run", "Task Run ID:")
        if accepted and run_id.strip():
            self.add_from_run_button.setEnabled(False)
            self.add_from_run_button.setText("Loading Task Run files…")
            self.files_from_run_requested.emit(run_id.strip())

    def set_source_run_files(self, files: list[dict]) -> None:
        self.add_from_run_button.setEnabled(True)
        self.add_from_run_button.setText("Add from Task Run")
        dialog = TaskRunFilePickerDialog(files, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_attachments([item["path"] for item in dialog.selected_files() if not item.get("artifact_uid")])
            self._artifact_inputs = dialog.selected_artifact_inputs()

    def set_source_run_error(self, message: str) -> None:
        self.add_from_run_button.setEnabled(True)
        self.add_from_run_button.setText("Add from Task Run")
        self.error_label.setText(message)

    def add_attachments(self, paths: list[str]) -> None:
        existing = {self.attachment_list.item(index).text() for index in range(self.attachment_list.count())}
        for path in paths:
            if path not in existing:
                self.attachment_list.addItem(path)
                existing.add(path)


class TaskRunFilePickerDialog(QDialog):
    def __init__(self, files: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add files from Task Run")
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select result or artifact files:"))
        self.file_list = QListWidget()
        for file in files:
            path = str(file["path"])
            item = QListWidgetItem(f"{file.get('kind') or 'File'} — {file.get('name') or Path(path).name}")
            item.setData(Qt.UserRole + 1, file)
            item.setCheckState(Qt.Unchecked)
            self.file_list.addItem(item)
        layout.addWidget(self.file_list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_files(self) -> list[dict]:
        return [
            dict(item.data(Qt.UserRole + 1) or {})
            for index in range(self.file_list.count())
            if (item := self.file_list.item(index)).checkState() == Qt.Checked
        ]

    def selected_artifact_inputs(self) -> list[dict]:
        return [
            {"artifact_uid": item["artifact_uid"], "alias": f"A{index}"}
            for index, item in enumerate(self.selected_files(), start=1)
            if item.get("artifact_uid")
        ]


class TasksView(QWidget):
    refresh_requested = Signal()
    create_requested = Signal()
    select_task_requested = Signal(str)
    edit_task_requested = Signal(str)
    delete_task_requested = Signal(str)
    run_task_requested = Signal(str)
    task_create_submitted = Signal(dict)
    task_edit_submitted = Signal(str, dict)
    task_run_submitted = Signal(str, dict)
    task_run_files_requested = Signal(object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.available_workers: list[str] = []
        self.editor: TaskEditorDialog | None = None
        self.runner: TaskRunDialog | None = None
        self.tasks_index: dict[str, dict] = {}
        self.profile_ids: list[str] = []

        root = QVBoxLayout(self)

        body = QHBoxLayout()
        self.list = TaskListView()
        self.list.refresh_requested.connect(self.refresh_requested.emit)
        self.list.create_task_requested.connect(self.create_requested.emit)
        self.list.select_task_requested.connect(self.select_task_requested.emit)
        self.list.setMaximumWidth(280)
        body.addWidget(self.list)

        self.detail = TaskDetailView()
        self.detail.refresh_requested.connect(self._on_task_refresh)
        self.detail.edit_requested.connect(self.edit_task_requested.emit)
        self.detail.delete_requested.connect(self.delete_task_requested.emit)
        self.detail.run_requested.connect(self.run_task_requested.emit)
        body.addWidget(self.detail, 1)

        root.addLayout(body, 1)
        self.set_tasks([])

    def set_tasks(self, tasks: list[dict]) -> None:
        self.tasks_index = {str(t.get("task_id")): t for t in tasks if t.get("task_id")}
        self.list.set_tasks(tasks)

    def set_task(self, task: dict, runs=None) -> None:
        self.tasks_index[str(task.get("task_id") or "")] = task
        self.detail.set_task(task, runs)

    def set_runs(self, task_id: str, runs) -> None:
        if self.detail.task_id == task_id:
            self.detail.set_runs(runs)

    def set_available_workers(self, workers) -> None:
        self.available_workers = list(workers)

    def set_profiles(self, profiles) -> None:
        self.profile_ids = [str(profile.get("profile_id")) for profile in profiles if profile.get("profile_id")]

    def show_create_editor(self) -> None:
        self.editor = TaskEditorDialog(available_workers=self.available_workers, profiles=self.profile_ids, parent=self)
        self.editor.accepted_payload.connect(self.task_create_submitted.emit)
        self.editor.open()

    def show_edit_editor(self, task_id: str) -> None:
        task = self.tasks_index.get(task_id)
        if not task:
            return
        self.editor = TaskEditorDialog(
            task=task, available_workers=self.available_workers, profiles=self.profile_ids, parent=self
        )
        self.editor.accepted_payload.connect(lambda payload, tid=task_id: self.task_edit_submitted.emit(tid, payload))
        self.editor.open()

    def show_run_dialog(self, task_id: str) -> None:
        task = self.tasks_index.get(task_id)
        if not task:
            return
        self.runner = TaskRunDialog(
            task=task, available_workers=self.available_workers, profiles=self.profile_ids, parent=self
        )
        self.runner.accepted.connect(lambda: self.task_run_submitted.emit(task_id, self.runner.overrides()))
        self.runner.files_from_run_requested.connect(
            lambda run_id, dialog=self.runner: self.task_run_files_requested.emit(dialog, run_id)
        )
        self.runner.open()

    def _on_task_refresh(self, task_id: str) -> None:
        self.select_task_requested.emit(task_id)
