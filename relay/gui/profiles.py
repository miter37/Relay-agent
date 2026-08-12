from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .design_typography import apply_type
from .design_widgets import IconButton, LabeledButton
from .scroll_state import preserve_scroll


class ProfilesView(QWidget):
    refresh_requested = Signal()
    create_requested = Signal(dict)
    update_requested = Signal(str, dict)
    delete_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.profiles: dict[str, dict] = {}
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        header = QHBoxLayout()
        list_title = QLabel("Profiles")
        list_title.setObjectName("sectionTitle")
        apply_type(list_title, "title.section")
        header.addWidget(list_title, 1)
        self.new_button = IconButton("plus", "Create a new Profile", tone="accent")
        self.new_button.clicked.connect(self._new)
        header.addWidget(self.new_button)
        left.addLayout(header)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._select)
        left.addWidget(self.list, 1)
        root.addLayout(left, 1)
        right = QVBoxLayout()
        self.title = QLabel("Select a Profile")
        self.title.setObjectName("pageTitle")
        apply_type(self.title, "title.detail")
        right.addWidget(self.title)
        self.notice = QLabel(
            "Built-in Profiles are read-only. Duplicate their intent in a new Profile to customize it."
        )
        self.notice.setObjectName("mutedText")
        apply_type(self.notice, "caption")
        self.notice.setWordWrap(True)
        right.addWidget(self.notice)
        form = QFormLayout()
        self.name = QLineEdit()
        self.description = QTextEdit()
        self.description.setMaximumHeight(70)
        self.instructions = QTextEdit()
        self.instructions.setMinimumHeight(180)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Execution instructions", self.instructions)
        right.addLayout(form, 1)
        row = QHBoxLayout()
        self.save = LabeledButton("check-circle", "Save Profile", tone="primary")
        self.save.clicked.connect(self._save)
        self.delete = IconButton("trash", "Delete this Profile", tone="danger")
        self.delete.clicked.connect(self._delete)
        row.addWidget(self.save)
        row.addWidget(self.delete)
        row.addStretch(1)
        right.addLayout(row)
        root.addLayout(right, 2)
        self._current: str | None = None
        self._set_editable(False)

    def set_profiles(self, profiles: list[dict]) -> None:
        self.profiles = {str(p["profile_id"]): p for p in profiles}
        with preserve_scroll(self.list):
            self.list.clear()
            for profile in profiles:
                item = QListWidgetItem(f"{profile['name']} {'· Built-in' if profile.get('builtin') else ''}")
                item.setData(32, profile["profile_id"])
                self.list.addItem(item)

    def _set_editable(self, editable: bool) -> None:
        for widget in (self.name, self.description, self.instructions, self.save, self.delete):
            widget.setEnabled(editable)

    def _select(self, item, _previous=None) -> None:
        profile = self.profiles.get(str(item.data(32))) if item else None
        self._current = profile.get("profile_id") if profile else None
        if not profile:
            self._set_editable(False)
            return
        self.title.setText(profile["name"])
        self.name.setText(profile["name"])
        self.description.setPlainText(profile.get("description") or "")
        self.instructions.setPlainText(profile.get("instructions") or "")
        self._set_editable(bool(profile.get("editable")))

    def _new(self) -> None:
        self._current = None
        self.title.setText("New Profile")
        self.name.clear()
        self.description.clear()
        self.instructions.clear()
        self._set_editable(True)

    def _payload(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "description": self.description.toPlainText().strip(),
            "instructions": self.instructions.toPlainText().strip(),
        }

    def _save(self) -> None:
        (
            self.update_requested.emit(self._current, self._payload())
            if self._current
            else self.create_requested.emit(self._payload())
        )

    def _delete(self) -> None:
        if self._current:
            self.delete_requested.emit(self._current)
