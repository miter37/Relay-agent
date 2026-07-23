from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from .agent_apps import AgentAppListView


class SettingsView(QWidget):
    autostart_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        general = QWidget()
        general_layout = QVBoxLayout(general)
        general_layout.addWidget(QLabel("<b>Settings</b>"))
        general_layout.addWidget(QLabel("Relay daemon"))
        self.autostart_status = QLabel("Auto-start status unavailable")
        self.autostart_status.setWordWrap(True)
        general_layout.addWidget(self.autostart_status)
        self.autostart_button = QPushButton("Enable auto-start")
        self.autostart_button.clicked.connect(self._toggle_autostart)
        general_layout.addWidget(self.autostart_button)
        general_layout.addStretch(1)
        self.tabs.addTab(general, "General")
        self.agent_apps_view = AgentAppListView()
        self.tabs.addTab(self.agent_apps_view, "Agent Apps")
        root.addWidget(self.tabs)
        self._enabled = False

    def set_agent_apps(self, agents: list[dict]) -> None:
        self.agent_apps_view.set_agents(agents)

    def set_autostart_status(self, status: dict) -> None:
        self._enabled = bool(status.get("enabled"))
        platform_name = str(status.get("platform") or "Unknown platform")
        if status.get("manual_start"):
            detail = "manual start required"
        elif status.get("field_validated"):
            detail = "field validated"
        else:
            detail = "adapter not field validated"
        state = "enabled" if self._enabled else "disabled"
        self.autostart_status.setText(f"{platform_name}: {state}; {detail}")
        self.autostart_button.setText("Disable auto-start" if self._enabled else "Enable auto-start")

    def _toggle_autostart(self) -> None:
        self.autostart_changed.emit(not self._enabled)
