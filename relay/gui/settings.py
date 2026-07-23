from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class SettingsView(QWidget):
    autostart_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("<b>Settings</b>"))
        root.addWidget(QLabel("Relay daemon"))
        self.autostart_status = QLabel("Auto-start status unavailable")
        self.autostart_status.setWordWrap(True)
        root.addWidget(self.autostart_status)
        self.autostart_button = QPushButton("Enable auto-start")
        self.autostart_button.clicked.connect(self._toggle_autostart)
        root.addWidget(self.autostart_button)
        root.addStretch(1)
        self._enabled = False

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
