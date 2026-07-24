from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from .agent_apps import AgentAppListView


class SettingsView(QWidget):
    autostart_changed = Signal(bool)
    antigravity_activate_requested = Signal()
    full_access_mode_changed = Signal(str, bool)

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

        general_layout.addWidget(QLabel("<br><b>Worker Security Bypasses</b>"))
        general_layout.addWidget(
            QLabel(
                "<i>These switches change the running daemon immediately. They disable permission checks or "
                "sandbox restrictions for the selected worker.</i>"
            )
        )
        self.codex_full_cb = QCheckBox("Codex: Full Access Mode (bypass sandbox and approvals)")
        self.codex_full_cb.toggled.connect(lambda checked: self.full_access_mode_changed.emit("codex", checked))
        general_layout.addWidget(self.codex_full_cb)
        self.claude_full_cb = QCheckBox("Claude: Full Access Mode (skip permissions)")
        self.claude_full_cb.toggled.connect(lambda checked: self.full_access_mode_changed.emit("claude", checked))
        general_layout.addWidget(self.claude_full_cb)
        self.agy_full_cb = QCheckBox("Antigravity: Full Access Mode (skip permissions)")
        self.agy_full_cb.toggled.connect(lambda checked: self.full_access_mode_changed.emit("antigravity", checked))
        general_layout.addWidget(self.agy_full_cb)

        general_layout.addWidget(QLabel("<br><b>Antigravity safety</b>"))
        self.antigravity_status = QLabel("Antigravity status unavailable")
        self.antigravity_status.setWordWrap(True)
        general_layout.addWidget(self.antigravity_status)
        self.antigravity_button = QPushButton("Verify & enable Antigravity")
        self.antigravity_button.clicked.connect(self._activate_antigravity)
        general_layout.addWidget(self.antigravity_button)
        general_layout.addStretch(1)
        self.tabs.addTab(general, "General")
        self.agent_apps_view = AgentAppListView()
        self.tabs.addTab(self.agent_apps_view, "Agent Apps")
        root.addWidget(self.tabs)
        self._enabled = False
        self._antigravity_pending = False

    def set_agent_apps(self, agents: list[dict]) -> None:
        self.agent_apps_view.set_agents(agents)

    def set_full_access_states(self, codex: bool, claude: bool, agy: bool) -> None:
        for worker, enabled in (("codex", codex), ("claude", claude), ("antigravity", agy)):
            self.set_full_access_state(worker, enabled)

    def set_full_access_state(self, worker: str, enabled: bool) -> None:
        checkbox = {
            "codex": self.codex_full_cb,
            "claude": self.claude_full_cb,
            "antigravity": self.agy_full_cb,
        }.get(worker)
        if checkbox is None:
            return
        checkbox.blockSignals(True)
        checkbox.setChecked(bool(enabled))
        checkbox.blockSignals(False)

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

    def set_antigravity_status(self, status: dict) -> None:
        state = str(status.get("state") or "unknown")
        version = str(status.get("version") or "unknown")
        audit = status.get("audit") or {}
        audited_at = audit.get("audited_at")
        if state == "enabled":
            text = f"Enabled; version {version}; deep audit passed"
            if audited_at:
                text += f" ({audited_at})"
            self.antigravity_button.setText("Antigravity enabled")
            self.antigravity_button.setEnabled(False)
        elif state == "ready":
            text = f"Ready to enable; version {version}; deep audit passed"
            self.antigravity_button.setText("Verify & enable Antigravity")
            self.antigravity_button.setEnabled(not self._antigravity_pending)
        elif state == "unavailable":
            text = "Antigravity CLI was not found. Install it and refresh this view."
            self.antigravity_button.setText("Verify & enable Antigravity")
            self.antigravity_button.setEnabled(False)
        elif state == "needs_audit":
            text = f"Deep audit required before enabling; version {version}"
            self.antigravity_button.setText("Verify & enable Antigravity")
            self.antigravity_button.setEnabled(not self._antigravity_pending)
        else:
            text = "Status unavailable"
            self.antigravity_button.setEnabled(not self._antigravity_pending)
        self.antigravity_status.setText(text)

    def set_antigravity_pending(self, pending: bool) -> None:
        self._antigravity_pending = pending
        self.antigravity_button.setEnabled(not pending)
        if pending:
            self.antigravity_button.setText("Checking & enabling…")

    def set_antigravity_error(self, message: str) -> None:
        self._antigravity_pending = False
        self.antigravity_status.setText(f"Activation failed: {message}")
        self.antigravity_button.setText("Verify & enable Antigravity")
        self.antigravity_button.setEnabled(True)

    def _activate_antigravity(self) -> None:
        self.antigravity_activate_requested.emit()
