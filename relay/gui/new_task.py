from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class JobFilePickerDialog(QDialog):
    def __init__(self, job_id: str, files: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add files from Job")
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Select result or artifact files from Job {job_id}:"))
        self.file_list = QListWidget()
        for file in files:
            path = str(file["path"])
            kind = str(file.get("kind") or "File")
            name = str(file.get("name") or Path(path).name)
            size = self._format_size(file.get("size"))
            item = QListWidgetItem(f"{kind} — {name}{f' ({size})' if size else ''}")
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            item.setCheckState(Qt.Unchecked)
            self.file_list.addItem(item)
        layout.addWidget(self.file_list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_paths(self) -> list[str]:
        return [
            str(item.data(Qt.UserRole))
            for index in range(self.file_list.count())
            if (item := self.file_list.item(index)).checkState() == Qt.Checked
        ]

    @staticmethod
    def _format_size(value) -> str:
        if value is None:
            return ""
        size = float(value)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return ""


class NewTaskView(QWidget):
    create_requested = Signal(dict)
    job_files_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._job_lookup_allowed = True
        self._job_lookup_pending = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>New Task</h2>"))
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Optional short title")
        form.addRow("Task name", self.title_edit)
        self.task_edit = QTextEdit()
        self.task_edit.setPlaceholderText("What should the agent do?")
        self.task_edit.setMinimumHeight(140)
        form.addRow("Task", self.task_edit)
        self.attachment_list = QListWidget()
        self.attachment_list.setMaximumHeight(90)
        attachment_row = QVBoxLayout()
        attachment_row.addWidget(self.attachment_list)
        attachment_buttons = QHBoxLayout()
        add_attachment = QPushButton("+ Add files")
        add_attachment.clicked.connect(self._choose_attachments)
        attachment_buttons.addWidget(add_attachment)
        self.add_from_job_button = QPushButton("+ Add from Job ID")
        self.add_from_job_button.clicked.connect(self._choose_job)
        attachment_buttons.addWidget(self.add_from_job_button)
        attachment_buttons.addStretch(1)
        attachment_row.addLayout(attachment_buttons)
        form.addRow(
            self._help_label(
                "Files",
                "Optional files supplied to the Agent as task attachments. "
                "You can also select delivered result or artifact files from an existing Job.",
            ),
            attachment_row,
        )
        self.worker_combo = QComboBox()
        self.worker_combo.addItems(["auto", "claude", "codex", "antigravity"])
        form.addRow("Agent", self.worker_combo)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("Default model")
        form.addRow("Model", self.model_edit)
        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(True)
        self.profile_combo.addItems(["web-research", "general-artifact", "analysis-only"])
        form.addRow("Profile", self.profile_combo)
        self.fallback_check = QCheckBox("Use another agent if this fails")
        form.addRow(
            self._help_label("Fallback", "If the selected Agent fails technically, try a configured fallback Agent."),
            self.fallback_check,
        )
        self.fallback_check.setChecked(True)
        layout.addLayout(form)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced options ▲")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_toggle)

        self.advanced_panel = QWidget()
        advanced_form = QFormLayout(self.advanced_panel)
        self.task_file_edit = QLineEdit()
        task_file_row = QHBoxLayout()
        task_file_row.addWidget(self.task_file_edit)
        task_file_button = QPushButton("Browse")
        task_file_button.clicked.connect(self._choose_task_file)
        task_file_row.addWidget(task_file_button)
        advanced_form.addRow(
            self._help_label(
                "Task file",
                "Use a UTF-8 text or Markdown file as the full task instruction. If both Task and Task file are set, Task file wins.",
            ),
            task_file_row,
        )
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 86400)
        self.timeout_spin.setValue(1200)
        advanced_form.addRow("Time limit (seconds)", self.timeout_spin)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["json", "txt"])
        advanced_form.addRow("Result type", self.format_combo)
        self.output_edit = QLineEdit()
        advanced_form.addRow(
            self._help_label("Result file", "Optional path for the final JSON or TXT result."), self.output_edit
        )
        self.artifact_edit = QLineEdit()
        advanced_form.addRow(
            self._help_label("Files folder", "Optional folder where generated artifact files are delivered."),
            self.artifact_edit,
        )
        self.target_edit = QLineEdit()
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_edit)
        target_button = QPushButton("Browse")
        target_button.clicked.connect(self._choose_target)
        target_row.addWidget(target_button)
        advanced_form.addRow(
            self._help_label(
                "Working folder",
                "The real folder the Agent must create or modify. Changed files are also copied to Files folder. "
                "Leave blank to detect one unambiguous absolute path from the task.",
            ),
            target_row,
        )
        self.request_id_edit = QLineEdit()
        advanced_form.addRow(
            self._help_label(
                "External Request ID",
                "Optional ID from an external system. Reusing it prevents duplicate work; it is not the Job ID.",
            ),
            self.request_id_edit,
        )
        self.force_new_check = QCheckBox("Create a new job even if a similar task exists")
        self.overwrite_check = QCheckBox("Replace an existing result file")
        advanced_form.addRow(
            self._help_label("Force new", "Ignore recent similar-task deduplication and always create a new Job."),
            self.force_new_check,
        )
        advanced_form.addRow(
            self._help_label("Overwrite", "Allow replacing an existing result file at the specified path."),
            self.overwrite_check,
        )
        self.force_new_check.setChecked(True)
        self.overwrite_check.setChecked(True)
        layout.addWidget(self.advanced_panel)
        buttons = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)
        self.create_button = QPushButton("Create task")
        self.create_button.clicked.connect(lambda: self.create_requested.emit(self.payload()))
        buttons.addWidget(self.create_button)
        layout.addLayout(buttons)

    @staticmethod
    def _help_label(label: str, explanation: str) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label))
        button = QToolButton()
        button.setText("?")
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFixedSize(22, 22)
        row.addWidget(button)
        help_text = QLabel(explanation)
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: #475569; font-size: 11px; padding: 2px 0;")
        help_text.hide()
        button.toggled.connect(help_text.setVisible)
        row.addWidget(help_text, 1)
        return container

    def _toggle_advanced(self, expanded: bool) -> None:
        self.advanced_panel.setVisible(expanded)
        self.advanced_toggle.setText("Advanced options ▲" if expanded else "Advanced options ▼")

    def _choose_task_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose task file")
        if path:
            self.task_file_edit.setText(path)

    def _choose_attachments(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files")
        self.add_attachments(paths)

    def _choose_job(self) -> None:
        job_id, accepted = QInputDialog.getText(
            self,
            "Add files from Job",
            "Job ID:",
            text="",
        )
        job_id = job_id.strip()
        if accepted and job_id:
            self.job_files_requested.emit(job_id)

    def choose_job_files(self, job_id: str, files: list[dict]) -> None:
        dialog = JobFilePickerDialog(job_id, files, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_attachments(dialog.selected_paths())

    def add_attachments(self, paths: list[str]) -> None:
        existing = {self.attachment_list.item(i).text() for i in range(self.attachment_list.count())}
        for path in paths:
            if path not in existing:
                self.attachment_list.addItem(path)
                existing.add(path)

    def set_job_file_lookup_enabled(self, enabled: bool) -> None:
        self._job_lookup_allowed = enabled
        self._update_job_lookup_button()

    def set_job_file_lookup_pending(self, pending: bool) -> None:
        self._job_lookup_pending = pending
        self._update_job_lookup_button()

    def _update_job_lookup_button(self) -> None:
        self.add_from_job_button.setEnabled(self._job_lookup_allowed and not self._job_lookup_pending)
        self.add_from_job_button.setText("Loading Job files…" if self._job_lookup_pending else "+ Add from Job ID")

    def _choose_target(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose working folder")
        if path:
            self.target_edit.setText(path)

    def payload(self) -> dict:
        payload = {
            "task": self.task_edit.toPlainText(),
            "title": self.title_edit.text().strip() or None,
            "task_file": self.task_file_edit.text().strip() or None,
            "worker": self.worker_combo.currentText(),
            "fallback": self.fallback_check.isChecked(),
            "result_format": self.format_combo.currentText(),
            "output_path": self.output_edit.text().strip() or None,
            "artifact_path": self.artifact_edit.text().strip() or None,
            "target_path": self.target_edit.text().strip() or None,
            "profile": self.profile_combo.currentText().strip() or "web-research",
            "timeout_seconds": self.timeout_spin.value(),
            "request_id": self.request_id_edit.text().strip() or None,
            "attachments": [self.attachment_list.item(i).text() for i in range(self.attachment_list.count())],
            "overwrite": self.overwrite_check.isChecked(),
            "force_new": self.force_new_check.isChecked(),
            "model": self.model_edit.text().strip() or None,
        }
        return {key: value for key, value in payload.items() if value is not None}

    def clear(self) -> None:
        for field in (
            self.title_edit,
            self.task_edit,
            self.task_file_edit,
            self.output_edit,
            self.artifact_edit,
            self.target_edit,
            self.request_id_edit,
            self.model_edit,
        ):
            field.clear()
        self.attachment_list.clear()
        self.worker_combo.setCurrentText("auto")
        self.profile_combo.setCurrentText("web-research")
        self.fallback_check.setChecked(True)
        self.timeout_spin.setValue(1200)
        self.format_combo.setCurrentText("json")
        self.force_new_check.setChecked(True)
        self.overwrite_check.setChecked(True)
