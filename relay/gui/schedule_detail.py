from __future__ import annotations

import json
from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ScheduleDetailView(QWidget):
    run_now_requested = Signal(str)
    pause_requested = Signal(str)
    resume_requested = Signal(str)
    edit_requested = Signal(str)
    copy_requested = Signal(str)
    delete_requested = Signal(str)
    open_output_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.schedule_id: str | None = None

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Schedule")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(self.title_label, 1)
        self.status_label = QLabel()
        header.addWidget(self.status_label)
        self.run_now_button = QPushButton("Run now")
        self.run_now_button.clicked.connect(self._run_now)
        header.addWidget(self.run_now_button)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self._pause)
        header.addWidget(self.pause_button)
        self.resume_button = QPushButton("Resume")
        self.resume_button.clicked.connect(self._resume)
        header.addWidget(self.resume_button)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._edit)
        header.addWidget(self.edit_button)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy)
        header.addWidget(self.copy_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete)
        header.addWidget(self.delete_button)
        self.open_output_button = QPushButton("Open output")
        self.open_output_button.clicked.connect(self._open_output)
        header.addWidget(self.open_output_button)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.overview = QTextBrowser()
        self.task_settings = QTextBrowser()
        self.run_history = QTextBrowser()
        self.tabs.addTab(self.overview, "Overview")
        self.tabs.addTab(self.task_settings, "Task settings")
        self.tabs.addTab(self.run_history, "Run history")
        root.addWidget(self.tabs, 1)

    def set_schedule(self, schedule: dict, runs: list[dict]) -> None:
        self.schedule_id = str(schedule.get("schedule_id") or "") or None
        self.title_label.setText(str(schedule.get("name") or self.schedule_id or "Schedule"))
        enabled = bool(schedule.get("enabled"))
        self.status_label.setText("Active" if enabled else "Paused")
        self.run_now_button.setEnabled(self.schedule_id is not None)
        self.pause_button.setEnabled(enabled)
        self.resume_button.setEnabled(not enabled)
        for button in (self.edit_button, self.copy_button, self.delete_button):
            button.setEnabled(self.schedule_id is not None)
        self.open_output_button.setEnabled(bool(schedule.get("output_root")))

        fields = (
            ("Next run", schedule.get("next_run_at_utc")),
            ("Last run", schedule.get("last_run")),
            ("Time zone", schedule.get("timezone")),
            ("Source job", schedule.get("source_job_id")),
            ("Output folder", schedule.get("output_root")),
            ("Attention", schedule.get("attention_code")),
        )
        self.overview.setHtml(
            "<table>{}</table>".format(
                "".join(
                    f"<tr><td><b>{escape(str(key))}</b></td><td>{escape(str(value or '—'))}</td></tr>"
                    for key, value in fields
                )
            )
        )
        self.task_settings.setHtml(self._format(schedule.get("task_settings") or schedule.get("rule") or {}))
        self.run_history.setHtml(self._format(runs))

    @staticmethod
    def _format(value) -> str:
        return f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))}</pre>"

    def _run_now(self) -> None:
        if self.schedule_id:
            self.run_now_requested.emit(self.schedule_id)

    def _pause(self) -> None:
        if self.schedule_id:
            self.pause_requested.emit(self.schedule_id)

    def _resume(self) -> None:
        if self.schedule_id:
            self.resume_requested.emit(self.schedule_id)

    def _edit(self) -> None:
        if self.schedule_id:
            self.edit_requested.emit(self.schedule_id)

    def _copy(self) -> None:
        if self.schedule_id:
            self.copy_requested.emit(self.schedule_id)

    def _delete(self) -> None:
        if self.schedule_id:
            self.delete_requested.emit(self.schedule_id)

    def _open_output(self) -> None:
        if self.schedule_id:
            self.open_output_requested.emit(self.schedule_id)
