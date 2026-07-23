from __future__ import annotations

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
        self.resize(620, 560)
        self._test_passed = False

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
        self.default_model_edit = QLineEdit()
        form.addRow("Default model", self.default_model_edit)
        self.network_check = QCheckBox("Needs network access")
        self.workspace_write_check = QCheckBox("Can write in workspace")
        self.workspace_write_check.setChecked(True)
        form.addRow("Safety", self.network_check)
        form.addRow("", self.workspace_write_check)
        root.addLayout(form)
        root.addWidget(QLabel("Deep test"))
        self.test_result = QLabel("Run the test before saving this Agent App.")
        self.test_result.setWordWrap(True)
        root.addWidget(self.test_result)

        actions = QHBoxLayout()
        self.test_button = QPushButton("Run test")
        self.test_button.clicked.connect(lambda: self.test_requested.emit(self.payload()))
        actions.addWidget(self.test_button)
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.save_button = QPushButton("Save agent")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.payload()))
        actions.addWidget(self.save_button)
        root.addLayout(actions)

    def payload(self) -> dict:
        argv = [line for line in self.argv_edit.toPlainText().splitlines() if line]
        return {
            "agent_id": self.id_edit.text().strip(),
            "display_name": self.name_edit.text().strip(),
            "description": self.description_edit.text().strip(),
            "executable": self.executable_edit.text().strip(),
            "argv": argv,
            "input_mode": self.input_mode.currentData(),
            "result_mode": self.result_mode.currentData(),
            "result_formats": ["json", "txt"],
            "supports_artifacts": True,
            "default_model": self.default_model_edit.text().strip(),
            "model_list_argv": [],
            "model_list_parser": "lines",
            "model_arg": ["--model", "{model}"],
            "safety": {
                "network": self.network_check.isChecked(),
                "workspace_write": self.workspace_write_check.isChecked(),
                "may_skip_permissions": False,
                "env_names": [],
            },
        }

    def set_agent(self, agent: dict) -> None:
        self.name_edit.setText(str(agent.get("display_name") or ""))
        self.id_edit.setText(str(agent.get("agent_id") or ""))
        self.id_edit.setEnabled(False)
        self.executable_edit.setText(str(agent.get("executable") or ""))
        self.description_edit.setText(str(agent.get("description") or ""))
        self.input_mode.setCurrentIndex(max(0, self.input_mode.findData(agent.get("input_mode"))))
        self.argv_edit.setPlainText("\n".join(agent.get("argv") or []))
        self.result_mode.setCurrentIndex(max(0, self.result_mode.findData(agent.get("result_mode"))))
        self.default_model_edit.setText(str(agent.get("default_model") or ""))
        safety = agent.get("safety") or {}
        self.network_check.setChecked(bool(safety.get("network")))
        self.workspace_write_check.setChecked(bool(safety.get("workspace_write")))

    def set_test_result(self, result: dict) -> None:
        healthy = result.get("status") == "healthy"
        self._test_passed = healthy
        self.test_result.setText("Passed" if healthy else str(result.get("error") or "Test failed"))
        self.save_button.setEnabled(healthy)


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
