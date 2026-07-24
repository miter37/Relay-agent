from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class NewTaskView(QWidget):
    create_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
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
        add_attachment = QPushButton("+ Add files")
        add_attachment.clicked.connect(self._choose_attachments)
        attachment_row.addWidget(add_attachment)
        form.addRow(
            self._help_label("Files", "Optional files supplied to the Agent as task attachments."), attachment_row
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
        for path in paths:
            if not any(self.attachment_list.item(i).text() == path for i in range(self.attachment_list.count())):
                self.attachment_list.addItem(path)

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
