from __future__ import annotations

from PySide6.QtCore import QSettings


class GuiState:
    def __init__(self, config):
        self.settings = QSettings(str(config.home / "config" / "gui.ini"), QSettings.IniFormat)

    def value(self, key: str, default=None):
        return self.settings.value(key, default)

    def set_value(self, key: str, value) -> None:
        self.settings.setValue(key, value)
        self.settings.sync()
