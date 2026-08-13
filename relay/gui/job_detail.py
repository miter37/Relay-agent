from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .artifacts import ArtifactExplorerView, ArtifactGroup, ArtifactRecord
from .design_tokens import COLORS, SPACING, status_presentation
from .design_typography import apply_type
from .design_widgets import IconButton, StatusBadge
from .json_display import render_json_html
from .scroll_state import set_html


class TaskRunDetailView(QWidget):
    cancel_requested = Signal(str)
    check_requested = Signal(str)
    rerun_requested = Signal(str)
    schedule_requested = Signal(str)
    tab_requested = Signal(str)
    open_folder_requested = Signal(str)
    open_log_requested = Signal(str)
    artifact_preview_requested = Signal(str, str)
    artifact_open_requested = Signal(str)
    artifact_folder_requested = Signal(str)
    log_options_changed = Signal()
    review_confirm_requested = Signal(str)
    review_rerun_requested = Signal(str, str)
    review_reject_requested = Signal(str, str)

    TAB_NAMES = ("Overview", "Progress", "Inputs", "Artifacts", "Logs", "Events")

    _OVERVIEW_LABEL_STYLE = (
        f"padding:6px 18px 6px 0; color:{COLORS['text.muted']}; "
        "font-size:11px; font-weight:600; vertical-align:top; width:150px;"
    )
    _OVERVIEW_VALUE_STYLE = (
        f"padding:6px 0; color:{COLORS['text.primary']}; vertical-align:top; "
        "word-wrap:break-word;"
    )

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
        self.review_action_row = QHBoxLayout()
        self.review_action_label = QLabel("")
        self.review_action_label.setObjectName("mutedText")
        self.review_action_row.addWidget(self.review_action_label, 1)
        self.review_confirm_button = IconButton("check-circle", "Confirm review", tone="accent")
        self.review_confirm_button.clicked.connect(self._emit_review_confirm)
        self.review_rerun_button = IconButton("rerun", "Rerun with feedback")
        self.review_rerun_button.clicked.connect(self._emit_review_rerun)
        self.review_reject_button = IconButton("x-circle", "Reject review", tone="danger")
        self.review_reject_button.clicked.connect(self._emit_review_reject)
        for button in (self.review_confirm_button, self.review_rerun_button, self.review_reject_button):
            self.review_action_row.addWidget(button)
        layout.addLayout(self.review_action_row)
        self._review_id: str | None = None
        self._review_action_pending = False
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
        self._content_cache: dict[str, str] = {}
        self._deferred_content: dict[str, str] = {}
        self.artifacts_view = ArtifactExplorerView()
        self.artifacts_view.preview_requested.connect(self.artifact_preview_requested.emit)
        self.artifacts_view.open_file_requested.connect(self.artifact_open_requested.emit)
        self.artifacts_view.open_folder_requested.connect(self.artifact_folder_requested.emit)
        self._result_artifact: ArtifactRecord | None = None
        self._file_artifacts: list[ArtifactRecord] = []
        self._overview_fields: tuple[tuple[str, object], ...] = ()
        self._requested_task = ""
        self._overview_details_expanded = False
        for name in self.TAB_NAMES:
            if name == "Artifacts":
                self.tabs.addTab(self.artifacts_view, name)
                continue
            browser = QTextBrowser()
            browser.setObjectName("overviewEvidencePane" if name == "Overview" else "evidencePane")
            browser.setOpenExternalLinks(False)
            browser.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            browser.selectionChanged.connect(lambda name=name: self._flush_deferred_content(name))
            if name == "Overview":
                browser.setLineWrapMode(QTextEdit.WidgetWidth)
                browser.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
                browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._browsers[name] = browser
            if name == "Overview":
                overview_page = QWidget()
                overview_layout = QVBoxLayout(overview_page)
                overview_layout.setContentsMargins(0, 0, 0, 0)
                overview_layout.setSpacing(0)
                overview_actions = QHBoxLayout()
                overview_actions.setContentsMargins(SPACING["md"], 0, SPACING["md"], 0)
                self.overview_result_label = QLabel("Result")
                self.overview_result_label.setObjectName("sectionTitle")
                self.overview_result_label.setStyleSheet(
                    f"color: {COLORS['accent.primary']}; font-weight: 700;"
                )
                overview_actions.addWidget(self.overview_result_label)
                self.overview_details_button = QToolButton()
                self.overview_details_button.setText("See more")
                self.overview_details_button.setCheckable(True)
                self.overview_details_button.setAutoRaise(True)
                self.overview_details_button.setToolTip("Show the full Task Run metadata")
                self.overview_details_button.toggled.connect(self._toggle_overview_details)
                overview_actions.addWidget(self.overview_details_button)
                overview_actions.addStretch(1)
                self.copy_answer_button = IconButton("copy", "Copy the answer")
                self.copy_answer_button.clicked.connect(self._copy_answer)
                overview_actions.addWidget(self.copy_answer_button)
                overview_layout.addLayout(overview_actions)
                overview_layout.addWidget(browser, 1)
                self.answer_browser = browser
                self.tabs.addTab(overview_page, name)
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
        self._set_review_action_state(False)

    def set_job(self, job: dict) -> None:
        job_id = str(job.get("task_run_id") or job.get("job_id") or "")
        if job_id != self.job_id:
            self._content_cache.clear()
            self._deferred_content.clear()
            for browser in self._browsers.values():
                browser.clear()
            self._overview_details_expanded = False
            self.overview_details_button.blockSignals(True)
            self.overview_details_button.setChecked(False)
            self.overview_details_button.blockSignals(False)
            self.set_answer(None)
            self.set_content("Logs", "")
            self._result_artifact = None
            self._file_artifacts = []
            self.artifacts_view.set_groups([], auto_select_primary=False)
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
        self._render_review_actions(job)
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
        self._overview_fields = fields
        self._requested_task = str(request_preview or "Task details are unavailable.")
        self._render_overview()
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
        self.set_content("Progress", self._format_progress(job.get("attempts", [])))
        review = job.get("review") or {}
        review_data = review.get("review") if isinstance(review.get("review"), dict) else review
        if review_data:
            current_round = review.get("current_round") or {}
            review_html = (
                f"<h3>Review status: {escape(str(review_data.get('status') or job.get('review_status') or ''))}</h3>"
                f"<p><b>Reviewer:</b> {escape(str(review_data.get('reviewer') or 'human'))}</p>"
                f"<p><b>Guidelines</b><br>{escape(str(review_data.get('guidelines') or 'No extra guidelines.')).replace(chr(10), '<br>')}</p>"
                f"<p><b>Current round:</b> {current_round.get('round_no') or 1} · "
                f"<b>Reruns:</b> {review_data.get('reruns_used', 0)}/{review_data.get('max_reruns', 0)}</p>"
                f"{self._format_json(review.get('artifacts') or job.get('artifacts') or [])}"
            )
        else:
            review_html = "<i>This Task Run does not require result review.</i>"
        self.set_content("Review", review_html)
        self.set_content("Events", self._format_json(job.get("events", [])))
        self.set_artifact_rows(job.get("artifacts", []))
        output_path = str(job.get("output_path") or "")
        if output_path:
            self._set_result_record(
                {
                    "artifact_uid": f"result:{self.job_id}",
                    "role": "result",
                    "relative_path": output_path,
                    "final_path": output_path,
                    "publication_status": "published",
                    "job_id": self.job_id,
                    "mime_type": "application/json" if str(job.get("format")) == "json" else "",
                    "is_primary": True,
                }
            )

    def set_artifact_rows(self, artifacts: list[dict] | None) -> None:
        self._file_artifacts = [
            ArtifactRecord.from_mapping(item, is_primary=False) for item in (artifacts or []) if isinstance(item, dict)
        ]
        self._refresh_artifacts()

    def set_result_payload(self, payload: dict) -> None:
        job_id = str(payload.get("job_id") or payload.get("task_run_id") or self.job_id or "")
        path = str(payload.get("path") or "")
        if job_id and path:
            uid = f"result:{job_id}"
            self.artifacts_view.cache_content(uid, payload)
            self._set_result_record(
                {
                    "artifact_uid": uid,
                    "role": "result",
                    "relative_path": path,
                    "final_path": path,
                    "mime_type": "application/json" if payload.get("format") == "json" else "",
                    "size": payload.get("size"),
                    "job_id": job_id,
                    "is_primary": True,
                }
            )

    def _set_result_record(self, raw: dict) -> None:
        self._result_artifact = ArtifactRecord.from_mapping(raw, is_primary=True)
        self._refresh_artifacts()

    def _refresh_artifacts(self) -> None:
        groups: list[ArtifactGroup] = []
        if self._result_artifact:
            groups.append(ArtifactGroup("Result", (self._result_artifact,)))
        if self._file_artifacts:
            groups.append(ArtifactGroup("Files", tuple(self._file_artifacts)))
        self.artifacts_view.set_groups(groups, auto_select_primary=True)

    def _render_review_actions(self, job: dict) -> None:
        review = job.get("review") or {}
        data = review.get("review") if isinstance(review.get("review"), dict) else review
        status = str(data.get("status") or job.get("review_status") or "")
        self._review_id = str(data.get("review_id") or review.get("review_id") or "") or None
        pending = bool(self._review_id and status in {"pending_human", "needs_human", "delivery_failed"})
        if pending:
            self.review_action_label.setText(
                f"Needs review · reruns {data.get('reruns_used', 0)}/{data.get('max_reruns', 0)} · "
                f"{data.get('reviewer') or 'human'}"
            )
            self.review_action_label.setToolTip(str(data.get("guidelines") or "No additional review guidelines."))
        else:
            self.review_action_label.clear()
        self._set_review_action_state(pending)

    def _set_review_action_state(self, enabled: bool) -> None:
        visible = bool(enabled and self._review_id)
        busy = self._review_action_pending
        self.review_action_label.setVisible(visible)
        for button in (self.review_confirm_button, self.review_rerun_button, self.review_reject_button):
            button.setVisible(visible)
            button.setEnabled(visible and not busy)

    def set_review_action_pending(self, pending: bool, review_id: str | None = None) -> None:
        if review_id and self._review_id and review_id != self._review_id:
            return
        self._review_action_pending = pending
        self._set_review_action_state(bool(self._review_id))

    def review_action_completed(self, review_id: str) -> None:
        if review_id and review_id == self._review_id:
            self._review_id = None
        self._review_action_pending = False
        self._set_review_action_state(False)

    def _emit_review_confirm(self) -> None:
        if self._review_id:
            self.set_review_action_pending(True)
            self.review_confirm_requested.emit(self._review_id)

    def _emit_review_rerun(self) -> None:
        if not self._review_id:
            return
        comment, accepted = QInputDialog.getMultiLineText(
            self, "Rerun with feedback", "What should change in the next attempt?", ""
        )
        if accepted and comment.strip():
            self.set_review_action_pending(True)
            self.review_rerun_requested.emit(self._review_id, comment.strip())

    def _emit_review_reject(self) -> None:
        if not self._review_id:
            return
        reason, accepted = QInputDialog.getMultiLineText(self, "Reject review", "Reason", "")
        if accepted and reason.strip():
            self.set_review_action_pending(True)
            self.review_reject_requested.emit(self._review_id, reason.strip())

    def set_content(self, tab_name: str, content: str) -> None:
        self._set_content(tab_name, content, force=False)

    def _set_content(self, tab_name: str, content: str, *, force: bool) -> None:
        browser = self._browsers.get(tab_name)
        if not browser:
            return
        if not force and self._content_cache.get(tab_name) == content:
            self._deferred_content.pop(tab_name, None)
            return
        if not force and browser.textCursor().hasSelection():
            self._deferred_content[tab_name] = content
            return
        self._deferred_content.pop(tab_name, None)
        self._content_cache[tab_name] = content
        bar = browser.verticalScrollBar()
        follow_tail = tab_name == "Logs" and self.auto_scroll_check.isChecked() and bar.value() >= bar.maximum() - 2
        if follow_tail:
            browser.setHtml(content)
            browser.moveCursor(QTextCursor.End)
        else:
            set_html(browser, content)

    def _flush_deferred_content(self, tab_name: str) -> None:
        browser = self._browsers.get(tab_name)
        content = self._deferred_content.get(tab_name)
        if browser is None or content is None or browser.textCursor().hasSelection():
            return
        self._set_content(tab_name, content, force=True)

    def set_answer(self, answer: str | None) -> None:
        self.answer_text = answer if isinstance(answer, str) else ""
        self.copy_answer_button.setEnabled(bool(self.answer_text))
        self._render_overview()

    @staticmethod
    def _format_json(value) -> str:
        return render_json_html(value)

    @staticmethod
    def _format_progress(attempts: object) -> str:
        if not isinstance(attempts, list) or not attempts:
            return '<i>No attempts have been recorded yet.</i>'
        rows: list[str] = []
        for index, attempt in enumerate(attempts, start=1):
            if not isinstance(attempt, dict):
                rows.append(
                    f'<div style="padding:12px 0; border-bottom:1px solid {COLORS["border.subtle"]};">'
                    f"{escape(str(attempt))}</div>"
                )
                continue
            attempt_no = attempt.get("attempt_id") or index
            details = (
                ("Status", attempt.get("status")),
                ("Agent", attempt.get("worker") or attempt.get("agent")),
                ("Started", attempt.get("started_at")),
                ("Finished", attempt.get("completed_at") or attempt.get("finished_at")),
                ("Error", attempt.get("error")),
            )
            detail_rows = "".join(
                f'<div style="padding-top:3px; color:{COLORS["text.secondary"]};">'
                f'<span style="color:{COLORS["text.muted"]};">{escape(label)}:</span> '
                f'{escape(str(value))}</div>'
                for label, value in details
                if value not in (None, "")
            )
            rows.append(
                f'<div style="padding:12px 0; border-bottom:1px solid {COLORS["border.subtle"]};">'
                f'<div style="color:{COLORS["text.primary"]}; font-weight:700;">Attempt {escape(str(attempt_no))}</div>'
                f"{detail_rows}</div>"
            )
        return "".join(rows)

    @staticmethod
    def _markdown_fragment(content: str) -> str:
        document = QTextDocument()
        document.setMarkdown(content)
        rendered = document.toHtml()
        body_start = rendered.find("<body")
        if body_start >= 0:
            body_start = rendered.find(">", body_start) + 1
            body_end = rendered.rfind("</body>")
            if body_start > 0 and body_end > body_start:
                return rendered[body_start:body_end]
        return escape(content).replace("\n", "<br>")

    def _render_overview(self, *, force: bool = False) -> None:
        if not hasattr(self, "overview_details_button"):
            return
        summary_labels = {label: value for label, value in self._overview_fields}
        summary_fields = (
            ("Registered name", summary_labels.get("Registered name")),
            ("Status", summary_labels.get("Status")),
            ("Actual agent", summary_labels.get("Actual agent")),
            ("Finished time", summary_labels.get("Finished")),
        )
        summary_cells = "".join(
            f'<td style="width:50%; padding:10px 12px; vertical-align:top;">'
            f'<div style="color:{COLORS["text.muted"]}; font-size:11px;">{escape(label)}</div>'
            f'<div style="padding-top:3px; color:{COLORS["text.primary"]}; font-weight:700;">'
            f"{escape(str(value or '—'))}</div></td>"
            for label, value in summary_fields
        )
        answer_html = (
            self._markdown_fragment(self.answer_text)
            if self.answer_text
            else f'<i style="color:{COLORS["text.muted"]};">No answer is available for this result.</i>'
        )
        details_html = ""
        if self._overview_details_expanded:
            detail_rows = "".join(
                self._overview_row(label, value or "—")
                for label, value in self._overview_fields
                if label not in {"Registered name", "Status", "Actual agent", "Finished"}
            )
            details_html = (
                f'<div style="margin-top:12px; padding:10px 0 4px 0; border-top:1px solid {COLORS["border.subtle"]};">'
                f'<div style="color:{COLORS["text.secondary"]}; font-weight:700;">Run details</div>'
                f'<table style="width:100%; border-collapse:collapse;">{detail_rows}</table></div>'
            )
        requested = escape(self._requested_task or "Task details are unavailable.").replace("\n", "<br>")
        content = (
            f'<div style="padding:2px 0 8px 0; border-bottom:1px solid {COLORS["border.subtle"]};">'
            f'<table style="width:100%; border-collapse:collapse;"><tr>{summary_cells}</tr></table>{details_html}</div>'
            f'<div style="margin-top:16px; padding-top:2px;">'
            f'<div style="color:{COLORS["accent.relay"]}; font-weight:700;">Answer</div>'
            f'<div style="margin-top:6px; color:{COLORS["text.primary"]};">{answer_html}</div></div>'
            f'<div style="margin-top:18px; padding-top:14px; border-top:1px solid {COLORS["border.subtle"]};">'
            f'<div style="color:{COLORS["state.info"]}; font-weight:700;">Requested Task</div>'
            f'<div style="margin-top:6px; color:{COLORS["text.primary"]}; white-space:pre-wrap; word-wrap:break-word;">'
            f"{requested}</div></div>"
        )
        if force:
            self._set_content("Overview", content, force=True)
        else:
            self.set_content("Overview", content)

    def _toggle_overview_details(self, expanded: bool) -> None:
        self._overview_details_expanded = expanded
        self.overview_details_button.setText("See less" if expanded else "See more")
        # This is an explicit user action. It must win over the polling guard
        # that otherwise defers updates while the QTextBrowser has a selection.
        self._render_overview(force=True)

    @classmethod
    def _overview_row(cls, label: str, value: object) -> str:
        return (
            f'<tr><td style="{cls._OVERVIEW_LABEL_STYLE}">{escape(str(label))}</td>'
            f'<td style="{cls._OVERVIEW_VALUE_STYLE}">{escape(str(value))}</td></tr>'
        )

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
