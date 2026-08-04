"""Phase 3 registered-Tasks GUI widgets.

The Task widgets only render state and emit signals. ``MainWindow`` owns
request dispatch and response correlation through ``GuiRpcClient``. All
destructive actions require explicit confirmation by the caller; the
widgets never delete or rerun a Task silently.
"""

from __future__ import annotations

import json
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_WORKER_CHOICES: tuple[str, ...] = ("auto", "claude", "codex", "antigravity")
_RESULT_FORMATS: tuple[str, ...] = ("json", "txt")
_PROFILE_CHOICES: tuple[str, ...] = ("web-research", "report", "code", "analysis")


def _format_fields(payload: dict) -> str:
    if not payload:
        return "<i>No details available.</i>"
    rows = "".join(
        f"<tr><td><b>{escape(str(key))}</b></td><td>{escape(str(value or '—'))}</td></tr>"
        for key, value in payload.items()
    )
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
        header.addWidget(QLabel("<b>Registered Tasks</b>"), 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self.refresh_button)
        self.create_button = QPushButton("New Task")
        self.create_button.clicked.connect(self.create_task_requested.emit)
        header.addWidget(self.create_button)
        layout.addLayout(header)
        self.count_label = QLabel("")
        self.count_label.setObjectName("mutedText")
        header.addWidget(self.count_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by name")
        self.search_edit.textChanged.connect(self._rerender)
        layout.addWidget(self.search_edit)
        self.list_widget = QListWidget()
        self.list_widget.itemActivated.connect(self._item_activated)
        layout.addWidget(self.list_widget, 1)

    def set_tasks(self, tasks: list[dict]) -> None:
        self.tasks = list(tasks)
        self.tasks_by_id = {str(t.get("task_id")): t for t in tasks if t.get("task_id")}
        self._rerender()

    def selected_task_id(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _rerender(self) -> None:
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


class TaskDetailView(QWidget):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    run_requested = Signal(str)
    refresh_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = None

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Task")
        self.title_label.setObjectName("pageTitle")
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
        self.instructions_browser = QTextBrowser()
        self.policy_browser = QTextBrowser()
        self.run_browser = QTextBrowser()
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
        self.overview_browser.setHtml(
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
            )
        )
        self.instructions_browser.setPlainText(str(task.get("instructions") or ""))
        self.policy_browser.setHtml(
            _format_fields(
                {
                    "Input schema": task.get("input_schema"),
                    "Output contract": task.get("output_contract"),
                    "Validation policy": task.get("validation_policy"),
                }
            )
        )
        self.run_browser.setHtml(self._format_runs(runs or []))

    def clear(self) -> None:
        self.task_id = None
        self.title_label.setText("Task")
        self.status_label.setText("")
        for browser in (self.overview_browser, self.instructions_browser, self.policy_browser, self.run_browser):
            browser.clear()
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_runs(self, runs) -> None:
        self.run_browser.setHtml(self._format_runs(runs))

    @staticmethod
    def _format_runs(runs) -> str:
        if not runs:
            return "<i>No Runs recorded for this Task yet.</i>"
        rows = "".join(
            "<tr>"
            f"<td>{escape(str(run.get('task_run_id') or run.get('job_id') or run.get('run_id') or '—'))}</td>"
            f"<td>{escape(str(run.get('status') or '—'))}</td>"
            f"<td>{escape(str(run.get('completed_at') or run.get('created_at') or '—'))}</td>"
            f"<td>{escape(str(run.get('actual_worker') or run.get('requested_worker') or '—'))}</td>"
            "</tr>"
            for run in runs
        )
        return f"<table><tr><th>Run</th><th>Status</th><th>When</th><th>Worker</th></tr>{rows}</table>"

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

    def __init__(self, *, task=None, available_workers=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Task" if task else "New Task")
        self.resize(640, 620)
        self._task_id = str(task.get("task_id") or "") if task else ""

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Name", self.name_edit)
        self.description_edit = QLineEdit()
        form.addRow("Description", self.description_edit)

        self.worker_combo = QComboBox()
        workers = list(_WORKER_CHOICES)
        for worker in available_workers or ():
            if worker and worker not in workers:
                workers.append(worker)
        self.worker_combo.addItems(workers)
        form.addRow("Default Worker", self.worker_combo)

        self.fallback_checkbox = QCheckBox("Allow Worker fallback")
        self.fallback_checkbox.setChecked(True)
        form.addRow("Fallback", self.fallback_checkbox)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 24 * 60 * 60)
        self.timeout_spin.setSpecialValueText("No timeout")
        self.timeout_spin.setValue(0)
        form.addRow("Timeout (seconds)", self.timeout_spin)

        self.profile_combo = QComboBox()
        self.profile_combo.addItems(_PROFILE_CHOICES)
        form.addRow("Profile", self.profile_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItems(_RESULT_FORMATS)
        form.addRow("Result format", self.format_combo)

        self.input_schema_edit = QTextEdit()
        self.input_schema_edit.setPlaceholderText("Optional JSON Schema for inputs")
        self.input_schema_edit.setAcceptRichText(False)
        self.input_schema_edit.setMinimumHeight(80)
        form.addRow("Input schema", self.input_schema_edit)

        self.output_contract_edit = QTextEdit()
        self.output_contract_edit.setPlaceholderText("Optional JSON describing the expected output shape")
        self.output_contract_edit.setAcceptRichText(False)
        self.output_contract_edit.setMinimumHeight(80)
        form.addRow("Output contract", self.output_contract_edit)

        self.validation_policy_edit = QLineEdit()
        self.validation_policy_edit.setPlaceholderText("Optional identifier, e.g. strict, lenient")
        form.addRow("Validation policy", self.validation_policy_edit)

        root.addLayout(form)

        root.addWidget(QLabel("<b>Instructions</b>"))
        self.instructions_edit = QTextEdit()
        self.instructions_edit.setAcceptRichText(False)
        self.instructions_edit.setMinimumHeight(180)
        root.addWidget(self.instructions_edit, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("errorText")
        root.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if task:
            self._populate(task)

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
        self.input_schema_edit.setPlainText(str(task.get("input_schema") or ""))
        self.output_contract_edit.setPlainText(str(task.get("output_contract") or ""))
        self.validation_policy_edit.setText(str(task.get("validation_policy") or ""))
        self.instructions_edit.setPlainText(str(task.get("instructions") or ""))

    def _on_save(self) -> None:
        try:
            payload = self.payload()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.accepted_payload.emit(payload)
        self.accept()

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
        input_schema_text = self.input_schema_edit.toPlainText().strip()
        if input_schema_text:
            try:
                json.loads(input_schema_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Input schema is not valid JSON: {exc}") from exc
            payload["input_schema"] = input_schema_text
        output_contract_text = self.output_contract_edit.toPlainText().strip()
        if output_contract_text:
            try:
                json.loads(output_contract_text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Output contract is not valid JSON: {exc}") from exc
            payload["output_contract"] = output_contract_text
        validation = self.validation_policy_edit.text().strip()
        if validation:
            payload["validation_policy"] = validation
        return payload

    @property
    def editing_task_id(self):
        return self._task_id or None


class TaskRunDialog(QDialog):
    accepted_overrides = Signal(dict)

    def __init__(self, *, task: dict, available_workers=None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Run Task · {task.get('name') or task.get('task_id') or 'Task'}")
        self.resize(480, 280)
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
        self.profile_combo.addItems(_PROFILE_CHOICES)
        form.addRow("Profile override", self.profile_combo)
        self.format_combo = QComboBox()
        self.format_combo.addItem("(default)")
        self.format_combo.addItems(_RESULT_FORMATS)
        form.addRow("Result format override", self.format_combo)
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Optional notes saved with the Run")
        form.addRow("Notes", self.notes_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self.accepted_overrides.emit(self.overrides())
        self.accept()

    def overrides(self) -> dict:
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
        notes = self.notes_edit.text().strip()
        if notes:
            overrides["notes"] = notes
        return overrides


class SaveRunAsTaskDialog(QDialog):
    accepted_payload = Signal(dict)

    def __init__(self, *, run_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Save Run as Task · {run_id[:8]}")
        self.resize(420, 220)
        layout = QVBoxLayout(self)
        info = QLabel("Promote this Run to a registered Task. Historical Runs are not modified.")
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Task name")
        form.addRow("Name", self.name_edit)
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("Optional description")
        form.addRow("Description", self.description_edit)
        layout.addLayout(form)
        self.error_label = QLabel("")
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.error_label.setText("Name is required.")
            return
        payload = {
            "name": name,
            "description": self.description_edit.text().strip() or None,
        }
        self.accepted_payload.emit(payload)
        self.accept()

    def payload(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.text().strip() or None,
        }


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.available_workers: list[str] = []
        self.editor: TaskEditorDialog | None = None
        self.runner: TaskRunDialog | None = None
        self.tasks_index: dict[str, dict] = {}

        root = QVBoxLayout(self)
        root.addWidget(QLabel("<h2>Registered Tasks</h2>"))

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

    def show_create_editor(self) -> None:
        self.editor = TaskEditorDialog(available_workers=self.available_workers, parent=self)
        self.editor.accepted_payload.connect(self.task_create_submitted.emit)
        self.editor.open()

    def show_edit_editor(self, task_id: str) -> None:
        task = self.tasks_index.get(task_id)
        if not task:
            return
        self.editor = TaskEditorDialog(task=task, available_workers=self.available_workers, parent=self)
        self.editor.accepted_payload.connect(lambda payload, tid=task_id: self.task_edit_submitted.emit(tid, payload))
        self.editor.open()

    def show_run_dialog(self, task_id: str) -> None:
        task = self.tasks_index.get(task_id)
        if not task:
            return
        self.runner = TaskRunDialog(task=task, available_workers=self.available_workers, parent=self)
        self.runner.accepted.connect(lambda: self.task_run_submitted.emit(task_id, self.runner.overrides()))
        self.runner.open()

    def _on_task_refresh(self, task_id: str) -> None:
        self.select_task_requested.emit(task_id)
