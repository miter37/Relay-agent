from __future__ import annotations

import json
from html import escape

from PySide6.QtCore import Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class JobDetailView(QWidget):
    cancel_requested = Signal(str)
    rerun_requested = Signal(str)
    schedule_requested = Signal(str)
    tab_requested = Signal(str)
    open_result_requested = Signal(str)
    open_folder_requested = Signal(str)
    open_log_requested = Signal(str)
    log_options_changed = Signal()

    TAB_NAMES = ("Overview", "Task", "Progress", "Answer", "Result", "Files", "Logs", "Events")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.job_id: str | None = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Job")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(self.title_label, 1)
        self.status_label = QLabel()
        header.addWidget(self.status_label)
        self.cancel_button = QPushButton("Stop task")
        self.cancel_button.clicked.connect(self._cancel)
        header.addWidget(self.cancel_button)
        self.rerun_button = QPushButton("Run again")
        self.rerun_button.clicked.connect(self._rerun)
        header.addWidget(self.rerun_button)
        self.schedule_button = QPushButton("Schedule")
        self.schedule_button.clicked.connect(self._schedule)
        header.addWidget(self.schedule_button)
        self.open_result_button = QPushButton("Open result")
        self.open_result_button.clicked.connect(self._open_result)
        header.addWidget(self.open_result_button)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        header.addWidget(self.open_folder_button)
        layout.addLayout(header)
        log_controls = QHBoxLayout()
        log_controls.addWidget(QLabel("Logs:"))
        self.attempt_combo = QComboBox()
        self.attempt_combo.setMinimumWidth(160)
        log_controls.addWidget(self.attempt_combo)
        self.stream_combo = QComboBox()
        self.stream_combo.addItems(["stdout", "stderr"])
        log_controls.addWidget(self.stream_combo)
        self.errors_only_check = QCheckBox("Errors only")
        log_controls.addWidget(self.errors_only_check)
        self.auto_scroll_check = QCheckBox("Auto-scroll")
        self.auto_scroll_check.setChecked(True)
        log_controls.addWidget(self.auto_scroll_check)
        self.open_log_button = QPushButton("Open full log")
        self.open_log_button.clicked.connect(self._open_log)
        log_controls.addWidget(self.open_log_button)
        log_controls.addStretch(1)
        for control in (self.attempt_combo, self.stream_combo, self.errors_only_check):
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(lambda _index: self.log_options_changed.emit())
            else:
                control.stateChanged.connect(lambda _state: self.log_options_changed.emit())
        layout.addLayout(log_controls)
        self.tabs = QTabWidget()
        self._browsers: dict[str, QTextBrowser] = {}
        for name in self.TAB_NAMES:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            self._browsers[name] = browser
            if name == "Answer":
                answer_page = QWidget()
                answer_layout = QVBoxLayout(answer_page)
                answer_layout.setContentsMargins(0, 0, 0, 0)
                answer_actions = QHBoxLayout()
                answer_actions.addStretch(1)
                self.copy_answer_button = QPushButton("Copy answer")
                self.copy_answer_button.clicked.connect(self._copy_answer)
                answer_actions.addWidget(self.copy_answer_button)
                answer_layout.addLayout(answer_actions)
                answer_layout.addWidget(browser, 1)
                self.answer_browser = browser
                self.tabs.addTab(answer_page, name)
            else:
                self.tabs.addTab(browser, name)
        self.tabs.currentChanged.connect(lambda index: self.tab_requested.emit(self.tabs.tabText(index)))
        layout.addWidget(self.tabs, 1)
        self.answer_text = ""
        self.set_answer(None)

    def set_job(self, job: dict) -> None:
        job_id = str(job.get("job_id") or "")
        if job_id != self.job_id:
            self.set_answer(None)
            self.set_content("Result", "")
        self.job_id = job_id
        self.title_label.setText(str(job.get("title") or self.job_id or "Job"))
        self.status_label.setText(str(job.get("status") or ""))
        actions = job.get("actions") or {}
        self.cancel_button.setEnabled(bool(actions.get("can_cancel")))
        self.rerun_button.setEnabled(bool(actions.get("can_rerun")))
        self.schedule_button.setEnabled(bool(actions.get("can_schedule")))
        self.open_result_button.setEnabled(bool(actions.get("can_open_result")))
        self.open_folder_button.setEnabled(bool(actions.get("can_open_folder")))
        self.attempt_combo.clear()
        for attempt in job.get("attempts") or []:
            attempt_id = attempt.get("attempt_id")
            if attempt_id is not None:
                self.attempt_combo.addItem(f"Attempt {attempt_id}: {attempt.get('worker') or 'agent'}", int(attempt_id))
        self.open_log_button.setEnabled(self.attempt_combo.count() > 0)
        fields = (
            ("Status", job.get("status")),
            ("Requested agent", job.get("requested_worker")),
            ("Actual agent", job.get("actual_worker") or job.get("requested_worker")),
            ("Model", (job.get("request") or {}).get("model") or job.get("model")),
            ("Profile", job.get("profile")),
            ("Created", job.get("created_at")),
            ("Started", job.get("started_at")),
            ("Finished", job.get("completed_at")),
            ("Source", job.get("submitted_via")),
            ("Result file", job.get("output_path")),
            ("Files folder", job.get("artifact_path")),
            ("Job ID", job.get("job_id")),
        )
        self.set_content(
            "Overview",
            "<table>{}</table>".format(
                "".join(
                    f"<tr><td><b>{escape(str(key))}</b></td><td>{escape(str(value or '—'))}</td></tr>"
                    for key, value in fields
                )
            ),
        )
        request = job.get("request") or {}
        task_text = request.get("task") or job.get("task_text") or job.get("task_preview")
        self.set_content("Task", escape(str(task_text or "Task details are hidden by your history settings.")))
        self.set_content("Progress", self._format_json(job.get("attempts", [])))
        self.set_content("Events", self._format_json(job.get("events", [])))
        self.set_content("Files", self._format_json(job.get("artifacts", [])))

    def set_content(self, tab_name: str, content: str) -> None:
        browser = self._browsers.get(tab_name)
        if browser:
            browser.setHtml(content)
            if tab_name == "Logs" and self.auto_scroll_check.isChecked():
                browser.moveCursor(QTextCursor.End)

    def set_answer(self, answer: str | None) -> None:
        self.answer_text = answer if isinstance(answer, str) else ""
        self.copy_answer_button.setEnabled(bool(self.answer_text))
        if self.answer_text:
            self.answer_browser.setMarkdown(self.answer_text)
        else:
            self.answer_browser.setHtml("<i>No answer is available for this result.</i>")

    @staticmethod
    def _format_json(value) -> str:
        return f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))}</pre>"

    def _cancel(self) -> None:
        if self.job_id:
            self.cancel_requested.emit(self.job_id)

    def _rerun(self) -> None:
        if self.job_id:
            self.rerun_requested.emit(self.job_id)

    def _schedule(self) -> None:
        if self.job_id:
            self.schedule_requested.emit(self.job_id)

    def _open_result(self) -> None:
        if self.job_id:
            self.open_result_requested.emit(self.job_id)

    def _open_folder(self) -> None:
        if self.job_id:
            self.open_folder_requested.emit(self.job_id)

    def _open_log(self) -> None:
        if self.job_id:
            self.open_log_requested.emit(self.job_id)

    def _copy_answer(self) -> None:
        if self.answer_text:
            QApplication.clipboard().setText(self.answer_text)

    def selected_attempt(self) -> dict | None:
        attempt_id = self.attempt_combo.currentData()
        if attempt_id is None:
            return None
        return {"attempt_id": int(attempt_id)}
