from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class AgentAppWizard(QDialog):
    test_requested = Signal(dict)
    save_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Agent App")
        self.resize(680, 760)
        self._test_passed = False
        self._test_token: str | None = None
        self._tested_payload: dict | None = None
        self._original_manifest: dict = {}
        self._loading = False

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Agent name", self.name_edit)
        self.id_edit = QLineEdit()
        form.addRow("Agent ID", self.id_edit)
        self.executable_edit = QLineEdit()
        form.addRow("Command", self.executable_edit)
        self.description_edit = QLineEdit()
        form.addRow("Description", self.description_edit)
        self.input_mode = QComboBox()
        self.input_mode.addItem("Request file", "request_file")
        self.input_mode.addItem("Standard input", "stdin")
        self.input_mode.addItem("Task argument", "task_arg")
        form.addRow("Input", self.input_mode)
        self.argv_edit = QTextEdit()
        self.argv_edit.setPlaceholderText("One argv item per line\nrun\n{request_file}\n{result_file}")
        self.argv_edit.setMinimumHeight(110)
        form.addRow("Arguments", self.argv_edit)
        self.result_mode = QComboBox()
        self.result_mode.addItem("Result file", "result_file")
        self.result_mode.addItem("Standard output", "stdout")
        form.addRow("Result", self.result_mode)
        self.json_format_check = QCheckBox("JSON")
        self.json_format_check.setChecked(True)
        self.txt_format_check = QCheckBox("Text")
        self.txt_format_check.setChecked(True)
        formats = QHBoxLayout()
        formats.addWidget(self.json_format_check)
        formats.addWidget(self.txt_format_check)
        form.addRow("Result formats", formats)
        self.artifacts_check = QCheckBox("Supports artifacts")
        self.artifacts_check.setChecked(True)
        form.addRow("", self.artifacts_check)
        self.default_model_edit = QLineEdit()
        form.addRow("Default model", self.default_model_edit)
        self.model_list_argv_edit = QTextEdit()
        self.model_list_argv_edit.setPlaceholderText("One argv item per line, for example:\nmodels\n--json")
        self.model_list_argv_edit.setMaximumHeight(70)
        form.addRow("Model list arguments", self.model_list_argv_edit)
        self.model_list_parser = QComboBox()
        self.model_list_parser.addItem("Lines", "lines")
        self.model_list_parser.addItem("JSON", "json")
        form.addRow("Model list parser", self.model_list_parser)
        self.model_list_timeout = QSpinBox()
        self.model_list_timeout.setRange(1, 300)
        self.model_list_timeout.setValue(30)
        form.addRow("Model list timeout", self.model_list_timeout)
        self.model_arg_edit = QTextEdit()
        self.model_arg_edit.setPlaceholderText("One argv item per line, for example:\n--model\n{model}")
        self.model_arg_edit.setPlainText("--model\n{model}")
        self.model_arg_edit.setMaximumHeight(70)
        form.addRow("Model arguments", self.model_arg_edit)
        self.network_check = QCheckBox("Needs network access")
        self.workspace_write_check = QCheckBox("Can write in workspace")
        self.workspace_write_check.setChecked(True)
        self.skip_permissions_check = QCheckBox("May skip permission checks")
        self.env_names_edit = QLineEdit()
        self.env_names_edit.setPlaceholderText("OPENAI_API_KEY, ANTHROPIC_API_KEY")
        form.addRow("Safety", self.network_check)
        form.addRow("", self.workspace_write_check)
        form.addRow("", self.skip_permissions_check)
        form.addRow("Environment names", self.env_names_edit)
        root.addLayout(form)
        self.change_warning = QLabel(
            "Saving a changed runtime definition disables the Agent until the tested definition is enabled again."
        )
        self.change_warning.setWordWrap(True)
        root.addWidget(self.change_warning)
        root.addWidget(QLabel("Deep test"))
        self.test_result = QLabel("Run the test before saving this Agent App.")
        self.test_result.setWordWrap(True)
        root.addWidget(self.test_result)

        actions = QHBoxLayout()
        self.test_button = QPushButton("Run test")
        self.test_button.clicked.connect(self._request_test)
        actions.addWidget(self.test_button)
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.save_button = QPushButton("Save agent")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._request_save)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        self._connect_changes()

    def payload(self) -> dict:
        argv = [line for line in self.argv_edit.toPlainText().splitlines() if line]
        value = deepcopy(self._original_manifest)
        safety = dict(value.get("safety") or {})
        safety.update(
            {
                "network": self.network_check.isChecked(),
                "workspace_write": self.workspace_write_check.isChecked(),
                "may_skip_permissions": self.skip_permissions_check.isChecked(),
                "env_names": [name.strip() for name in self.env_names_edit.text().split(",") if name.strip()],
            }
        )
        value.update(
            {
                "agent_id": self.id_edit.text().strip(),
                "display_name": self.name_edit.text().strip(),
                "description": self.description_edit.text().strip(),
                "executable": self.executable_edit.text().strip(),
                "argv": argv,
                "input_mode": self.input_mode.currentData(),
                "result_mode": self.result_mode.currentData(),
                "result_formats": [
                    name
                    for name, checked in (
                        ("json", self.json_format_check.isChecked()),
                        ("txt", self.txt_format_check.isChecked()),
                    )
                    if checked
                ],
                "supports_artifacts": self.artifacts_check.isChecked(),
                "default_model": self.default_model_edit.text().strip(),
                "model_list_argv": [line for line in self.model_list_argv_edit.toPlainText().splitlines() if line],
                "model_list_parser": self.model_list_parser.currentData(),
                "model_list_timeout_seconds": self.model_list_timeout.value(),
                "model_arg": [line for line in self.model_arg_edit.toPlainText().splitlines() if line],
                "safety": safety,
            }
        )
        return value

    def set_agent(self, agent: dict) -> None:
        self._loading = True
        self._original_manifest = deepcopy(agent)
        self._original_manifest.pop("manifest_hash", None)
        self.name_edit.setText(str(agent.get("display_name") or ""))
        self.id_edit.setText(str(agent.get("agent_id") or ""))
        self.id_edit.setEnabled(False)
        self.executable_edit.setText(str(agent.get("executable") or ""))
        self.description_edit.setText(str(agent.get("description") or ""))
        self.input_mode.setCurrentIndex(max(0, self.input_mode.findData(agent.get("input_mode"))))
        self.argv_edit.setPlainText("\n".join(agent.get("argv") or []))
        self.result_mode.setCurrentIndex(max(0, self.result_mode.findData(agent.get("result_mode"))))
        formats = agent.get("result_formats") or []
        self.json_format_check.setChecked("json" in formats)
        self.txt_format_check.setChecked("txt" in formats)
        self.artifacts_check.setChecked(bool(agent.get("supports_artifacts")))
        self.default_model_edit.setText(str(agent.get("default_model") or ""))
        self.model_list_argv_edit.setPlainText("\n".join(agent.get("model_list_argv") or []))
        self.model_list_parser.setCurrentIndex(max(0, self.model_list_parser.findData(agent.get("model_list_parser"))))
        self.model_list_timeout.setValue(int(agent.get("model_list_timeout_seconds", 30)))
        self.model_arg_edit.setPlainText("\n".join(agent.get("model_arg") or []))
        safety = agent.get("safety") or {}
        self.network_check.setChecked(bool(safety.get("network")))
        self.workspace_write_check.setChecked(bool(safety.get("workspace_write")))
        self.skip_permissions_check.setChecked(bool(safety.get("may_skip_permissions")))
        self.env_names_edit.setText(", ".join(safety.get("env_names") or []))
        self._loading = False
        self._invalidate_test()

    def set_test_result(self, result: dict, *, test_token: str | None, tested_payload: dict) -> None:
        healthy = result.get("status") == "healthy" and bool(test_token) and self.payload() == tested_payload
        self._test_passed = healthy
        self._test_token = test_token if healthy else None
        self._tested_payload = deepcopy(tested_payload) if healthy else None
        self.test_result.setText("Passed" if healthy else str(result.get("error") or "Test failed"))
        self.save_button.setEnabled(healthy)

    def _connect_changes(self) -> None:
        for edit in (
            self.name_edit,
            self.id_edit,
            self.executable_edit,
            self.description_edit,
            self.default_model_edit,
            self.env_names_edit,
        ):
            edit.textChanged.connect(self._invalidate_test)
        for edit in (self.argv_edit, self.model_list_argv_edit, self.model_arg_edit):
            edit.textChanged.connect(self._invalidate_test)
        for combo in (self.input_mode, self.result_mode, self.model_list_parser):
            combo.currentIndexChanged.connect(self._invalidate_test)
        for check in (
            self.json_format_check,
            self.txt_format_check,
            self.artifacts_check,
            self.network_check,
            self.workspace_write_check,
            self.skip_permissions_check,
        ):
            check.toggled.connect(self._invalidate_test)
        self.model_list_timeout.valueChanged.connect(self._invalidate_test)

    def _invalidate_test(self, *_args) -> None:
        if self._loading:
            return
        self._test_passed = False
        self._test_token = None
        self._tested_payload = None
        self.save_button.setEnabled(False)
        self.test_result.setText("Run the test for the current definition before saving.")

    def _request_test(self) -> None:
        payload = self.payload()
        if not payload["agent_id"] or not payload["executable"] or not payload["argv"]:
            self.test_result.setText("Agent ID, command, and at least one argument are required.")
            return
        if not payload["result_formats"]:
            self.test_result.setText("Select at least one result format.")
            return
        self._invalidate_test()
        self.test_result.setText("Testing…")
        self.test_requested.emit(payload)

    def _request_save(self) -> None:
        if not self._test_passed or not self._test_token or self.payload() != self._tested_payload:
            self._invalidate_test()
            return
        self.save_requested.emit({**self.payload(), "test_token": self._test_token})


class AgentAppListView(QWidget):
    create_requested = Signal()
    edit_requested = Signal(str)
    test_requested = Signal(str)
    enabled_requested = Signal(str, bool)
    delete_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Agent Apps</b>"), 1)
        self.add_button = QPushButton("+ Add agent app")
        self.add_button.clicked.connect(self.create_requested)
        header.addWidget(self.add_button)
        root.addLayout(header)
        self.agent_list = QListWidget()
        self.agent_list.itemClicked.connect(self._select)
        root.addWidget(self.agent_list, 1)
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._edit)
        actions.addWidget(self.edit_button)
        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test)
        actions.addWidget(self.test_button)
        self.toggle_button = QPushButton("Enable")
        self.toggle_button.clicked.connect(self._toggle)
        actions.addWidget(self.toggle_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete)
        actions.addWidget(self.delete_button)
        root.addLayout(actions)
        self._selected: dict | None = None
        self._agents: dict[str, dict] = {}
        self._set_actions(False)

    def set_agents(self, agents: list[dict]) -> None:
        self._selected = None
        self._agents = {str(item.get("agent_id")): item for item in agents if item.get("agent_id")}
        self.agent_list.clear()
        for agent in agents:
            status = {
                "ready": "Ready",
                "needs_test": "Needs a test",
                "disabled": "Off",
                "unavailable": "Unavailable",
            }.get(agent.get("status"), str(agent.get("status") or "Unknown"))
            builtin = "Built in" if agent.get("builtin") else "Added by you"
            item = QListWidgetItem(f"{agent.get('display_name') or agent.get('agent_id')} · {status} · {builtin}")
            item.setData(Qt.UserRole, agent.get("agent_id"))
            self.agent_list.addItem(item)
        self._set_actions(False)

    def _select(self, item: QListWidgetItem) -> None:
        self._selected = self._agents.get(str(item.data(Qt.UserRole)))
        self._set_actions(self._selected is not None)

    def _set_actions(self, enabled: bool) -> None:
        if self._selected and self._selected.get("builtin"):
            enabled = False
        for button in (self.edit_button, self.test_button, self.toggle_button, self.delete_button):
            button.setEnabled(enabled)
        if enabled and self._selected:
            self.toggle_button.setText("Disable" if self._selected.get("enabled") else "Enable")

    def _edit(self) -> None:
        if self._selected:
            self.edit_requested.emit(str(self._selected["agent_id"]))

    def _test(self) -> None:
        if self._selected:
            self.test_requested.emit(str(self._selected["agent_id"]))

    def _toggle(self) -> None:
        if self._selected:
            self.enabled_requested.emit(str(self._selected["agent_id"]), not bool(self._selected.get("enabled")))

    def _delete(self) -> None:
        if self._selected:
            self.delete_requested.emit(str(self._selected["agent_id"]))
