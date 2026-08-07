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
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .design_tokens import COLORS, status_presentation
from .design_typography import apply_type
from .design_widgets import IconButton, StatusBadge


class TaskRunDetailView(QWidget):
    cancel_requested = Signal(str)
    check_requested = Signal(str)
    rerun_requested = Signal(str)
    schedule_requested = Signal(str)
    tab_requested = Signal(str)
    open_folder_requested = Signal(str)
    open_log_requested = Signal(str)
    log_options_changed = Signal()

    TAB_NAMES = ("Overview", "Task", "Inputs", "Progress", "Answer", "Result", "Files", "Logs", "Events")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.job_id: str | None = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Task Run")
        self.title_label.setObjectName("pageTitle")
        apply_type(self.title_label, "title.detail")
        header.addWidget(self.title_label, 1)
        self.status_label = StatusBadge()
        header.addWidget(self.status_label)
        self.cancel_button = IconButton("stop", "Stop this Task Run", tone="danger")
        self.cancel_button.clicked.connect(self._cancel)
        header.addWidget(self.cancel_button)
        self.check_button = IconButton("activity", "Check progress now")
        self.check_button.clicked.connect(self._check)
        header.addWidget(self.check_button)
        self.rerun_button = IconButton("rerun", "Run again with the same inputs", tone="accent")
        self.rerun_button.clicked.connect(self._rerun)
        header.addWidget(self.rerun_button)
        self.schedule_button = IconButton("clock", "Create a Schedule from this Run")
        self.schedule_button.clicked.connect(self._schedule)
        header.addWidget(self.schedule_button)
        self.open_folder_button = IconButton("folder-open", "Open the output folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        header.addWidget(self.open_folder_button)
        layout.addLayout(header)
        log_controls = QHBoxLayout()
        log_controls.addWidget(QLabel("Logs:"))
        self.attempt_combo = QComboBox()
        self.attempt_combo.setMinimumWidth(160)
        log_controls.addWidget(self.attempt_combo)
        self.stream_combo = QComboBox()
        self.stream_combo.addItems(["stdout", "stderr", "Check results"])
        log_controls.addWidget(self.stream_combo)
        self.errors_only_check = QCheckBox("Errors only")
        log_controls.addWidget(self.errors_only_check)
        self.auto_scroll_check = QCheckBox("Auto-scroll")
        self.auto_scroll_check.setChecked(True)
        log_controls.addWidget(self.auto_scroll_check)
        self.open_log_button = IconButton("file-text", "Open the full log file")
        self.open_log_button.clicked.connect(self._open_log)
        log_controls.addWidget(self.open_log_button)
        log_controls.addStretch(1)
        self.attempt_combo.currentIndexChanged.connect(lambda _index: self.log_options_changed.emit())
        self.stream_combo.currentIndexChanged.connect(self._stream_changed)
        self.errors_only_check.stateChanged.connect(lambda _state: self.log_options_changed.emit())
        layout.addLayout(log_controls)
        self.tabs = QTabWidget()
        self._browsers: dict[str, QTextBrowser] = {}
        for name in self.TAB_NAMES:
            browser = QTextBrowser()
            browser.setObjectName("evidencePane")
            browser.setOpenExternalLinks(False)
            self._browsers[name] = browser
            if name == "Answer":
                answer_page = QWidget()
                answer_layout = QVBoxLayout(answer_page)
                answer_layout.setContentsMargins(0, 0, 0, 0)
                answer_actions = QHBoxLayout()
                answer_actions.addStretch(1)
                self.copy_answer_button = IconButton("copy", "Copy the answer")
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
        self.task_text = ""
        self._check_pending = False
        self._can_check_progress = False
        # No Run is selected yet, so no Run action applies. ``set_job`` re-shows
        # each button according to the Run's own ``actions`` payload.
        for button in (
            self.cancel_button,
            self.check_button,
            self.rerun_button,
            self.schedule_button,
            self.open_folder_button,
        ):
            button.setVisible(False)
        self.set_answer(None)

    def set_job(self, job: dict) -> None:
        job_id = str(job.get("task_run_id") or job.get("job_id") or "")
        if job_id != self.job_id:
            self.set_answer(None)
            self.set_content("Result", "")
            self.set_content("Logs", "")
            self._check_pending = False
            self.stream_combo.blockSignals(True)
            self.stream_combo.setCurrentText("stdout")
            self.stream_combo.blockSignals(False)
        self.job_id = job_id
        self.title_label.setText(str(job.get("title") or self.job_id or "Task Run"))
        status = str(job.get("status") or "UNKNOWN")
        self.status_label.set_status(status)
        actions = job.get("actions") or {}
        can_cancel = bool(actions.get("can_cancel"))
        self.cancel_button.setVisible(can_cancel)
        self.cancel_button.setEnabled(can_cancel)
        self._can_check_progress = bool(actions.get("can_check_progress"))
        self.check_button.setVisible(self._can_check_progress)
        self.check_button.setEnabled(self._can_check_progress and not self._check_pending)
        can_rerun = bool(actions.get("can_rerun"))
        self.rerun_button.setVisible(can_rerun)
        self.rerun_button.setEnabled(can_rerun)
        self.rerun_button.set_tooltip("Creates a new Task Run using this Run's saved Task snapshot and input values.")
        can_schedule = bool(actions.get("can_schedule"))
        self.schedule_button.setVisible(can_schedule)
        self.schedule_button.setEnabled(can_schedule)
        self.schedule_button.set_tooltip(
            "Service isolation must be acknowledged before saving a schedule."
            if actions.get("schedule_requires_isolation")
            else "Schedule this completed task"
        )
        can_open_folder = bool(actions.get("can_open_folder"))
        self.open_folder_button.setVisible(can_open_folder)
        self.open_folder_button.setEnabled(can_open_folder)
        self.attempt_combo.blockSignals(True)
        self.attempt_combo.clear()
        for attempt in job.get("attempts") or []:
            attempt_id = attempt.get("attempt_id")
            if attempt_id is not None:
                self.attempt_combo.addItem(f"Attempt {attempt_id}: {attempt.get('worker') or 'agent'}", int(attempt_id))
        self.attempt_combo.blockSignals(False)
        self.open_log_button.setEnabled(self.attempt_combo.count() > 0)
        self._update_log_controls()
        fields = (
            ("Status", job.get("status")),
            ("Registered name", job.get("title")),
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
            ("Working folder", (job.get("request") or {}).get("target_path")),
            ("Task Run ID", job.get("task_run_id") or job.get("job_id")),
        )
        request = job.get("request") or {}
        task_text = str(request.get("task") or job.get("task_text") or job.get("task_preview") or "").strip()
        self.task_text = task_text
        request_preview = job.get("task_preview") or task_text
        self.set_content(
            "Overview",
            "<table>{}</table>{}".format(
                "".join(
                    f"<tr><td><b>{escape(str(key))}</b></td><td>{escape(str(value or '—'))}</td></tr>"
                    for key, value in fields
                ),
                f"<p><b>Requested task</b></p><pre>{escape(str(request_preview or 'Task details are unavailable.'))}</pre>",
            ),
        )
        self.set_content("Task", escape(str(task_text or "Task details are hidden by your history settings.")))
        task_inputs = job.get("task_inputs") or {}
        warning = job.get("input_integrity_warning")
        task_input_html = (
            self._format_json(task_inputs) if task_inputs else "<i>No Task input values were supplied.</i>"
        )
        artifact_html = self._format_json(job.get("lineage") or job.get("inputs") or [])
        self.set_content(
            "Inputs",
            "<h3>Task input values</h3>"
            + (f"<p>{escape(str(warning))}</p>" if warning else "")
            + task_input_html
            + "<h3>Artifact inputs and lineage</h3>"
            + artifact_html,
        )
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

    def _check(self) -> None:
        if self.job_id and not self._check_pending:
            self.check_requested.emit(self.job_id)

    def _rerun(self) -> None:
        if self.job_id:
            self.rerun_requested.emit(self.job_id)

    def _schedule(self) -> None:
        if self.job_id:
            self.schedule_requested.emit(self.job_id)

    def _open_folder(self) -> None:
        if self.job_id:
            self.open_folder_requested.emit(self.job_id)

    def _open_log(self) -> None:
        if self.job_id:
            self.open_log_requested.emit(self.job_id)

    def _stream_changed(self, _index: int) -> None:
        self._update_log_controls()
        self.log_options_changed.emit()

    def _update_log_controls(self) -> None:
        checks = self.is_check_stream()
        self.attempt_combo.setEnabled(not checks)
        self.errors_only_check.setEnabled(not checks)
        self.open_log_button.setEnabled(not checks and self.attempt_combo.count() > 0)

    def is_check_stream(self) -> bool:
        return self.stream_combo.currentText() == "Check results"

    def select_check_results(self) -> None:
        self.stream_combo.setCurrentText("Check results")
        self.tabs.setCurrentIndex(self.TAB_NAMES.index("Logs"))

    def set_check_pending(self, pending: bool) -> None:
        self._check_pending = pending
        self.check_button.set_tooltip("Checking…" if pending else "Check progress now")
        self.check_button.setEnabled(self._can_check_progress and not pending)

    def _copy_answer(self) -> None:
        if self.answer_text:
            QApplication.clipboard().setText(self.answer_text)

    @staticmethod
    def _status_text(status: str) -> str:
        return {
            "COMPLETED": "✓ COMPLETED",
            "PARTIAL": "◐ PARTIAL",
            "FAILED": "× FAILED",
            "CANCELLED": "— CANCELLED",
        }.get(status, f"● {status}")

    @staticmethod
    def _status_style(status: str) -> str:
        presentation = status_presentation(status)
        foreground = COLORS[presentation.color_token]
        background = COLORS["bg.surface"]
        border = COLORS[presentation.color_token]
        return (
            f"QLabel {{ color: {foreground}; background: {background}; border: 1px solid {border}; "
            "border-radius: 6px; padding: 5px 10px; font-size: 13px; font-weight: 700; }"
        )

    def selected_attempt(self) -> dict | None:
        attempt_id = self.attempt_combo.currentData()
        if attempt_id is None:
            return None
        return {"attempt_id": int(attempt_id)}


# Compatibility import for existing GUI extensions and tests. New code should
# use the canonical TaskRunDetailView name.
JobDetailView = TaskRunDetailView
