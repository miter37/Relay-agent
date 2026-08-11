"""Human-friendly inbox for Task and Project result review gates."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ReviewsView(QWidget):
    select_review_requested = Signal(str)
    confirm_requested = Signal(str)
    rerun_requested = Signal(str, str)
    reject_requested = Signal(str, str)
    refresh_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._reviews: dict[str, dict] = {}
        self._current_id: str | None = None
        root = QHBoxLayout(self)
        self.list = QListWidget()
        self.list.setMinimumWidth(280)
        self.list.currentItemChanged.connect(self._select_item)
        root.addWidget(self.list)
        panel = QVBoxLayout()
        self.title = QLabel("Select a review")
        self.title.setObjectName("pageTitle")
        panel.addWidget(self.title)
        self.detail = QTextBrowser()
        panel.addWidget(self.detail, 1)
        self.comment = QTextEdit()
        self.comment.setPlaceholderText("Optional feedback for a rerun")
        self.comment.setMaximumHeight(84)
        panel.addWidget(self.comment)
        actions = QHBoxLayout()
        self.confirm = QPushButton("Confirm & publish")
        self.rerun = QPushButton("Rerun with feedback")
        self.reject = QPushButton("Reject")
        self.refresh = QPushButton("Refresh")
        self.confirm.clicked.connect(lambda: self._emit_confirm())
        self.rerun.clicked.connect(lambda: self._emit_rerun())
        self.reject.clicked.connect(lambda: self._emit_reject())
        self.refresh.clicked.connect(self.refresh_requested.emit)
        actions.addWidget(self.confirm)
        actions.addWidget(self.rerun)
        actions.addWidget(self.reject)
        actions.addStretch(1)
        actions.addWidget(self.refresh)
        panel.addLayout(actions)
        root.addLayout(panel, 1)
        self._set_action_state(False)

    def set_reviews(self, reviews: list[dict]) -> None:
        self._reviews = {str(item.get("review_id")): item for item in reviews if item.get("review_id")}
        selected = self._current_id
        self.list.blockSignals(True)
        self.list.clear()
        for review in reviews:
            review_id = str(review.get("review_id"))
            scope = "Project" if review.get("scope_type") == "project" else "Task"
            status = str(review.get("status") or "pending").replace("_", " ").title()
            label = f"{scope} · {review.get('task_title') or review.get('node_id') or review_id[:8]}\n{status}"
            item = QListWidgetItem(label)
            item.setData(256, review_id)
            self.list.addItem(item)
            if review_id == selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        if not self.list.count():
            self._current_id = None
            self.title.setText("No reviews waiting")
            self.detail.setHtml("<p>When a Task or Project result needs review, it will appear here.</p>")
            self._set_action_state(False)

    def set_review(self, review: dict) -> None:
        data = review.get("review") if isinstance(review.get("review"), dict) else review
        review_id = str(data.get("review_id") or self._current_id or "")
        if review_id:
            self._reviews[review_id] = data
            self._current_id = review_id
        current = review.get("current_round") or {}
        task_run = review.get("task_run") or {}
        lines = [
            f"<h2>{escape(str(data.get('scope_type') or 'Result').title())} review</h2>",
            f"<p><b>Status:</b> {escape(str(data.get('status') or ''))} · <b>Round:</b> {data.get('current_round') or current.get('round_no') or 1}</p>",
            f"<p><b>Reviewer:</b> {escape(str(data.get('reviewer') or 'human'))} · <b>Automatic reruns:</b> {data.get('reruns_used', 0)}/{data.get('max_reruns', 0)}</p>",
            f"<p><b>Guidelines</b><br>{escape(str(data.get('guidelines') or 'No extra guidelines.')).replace(chr(10), '<br>')}</p>",
            f"<p><b>Task Run:</b> {escape(str(task_run.get('job_id') or current.get('task_run_id') or 'Unavailable'))}</p>",
        ]
        artifacts = review.get("artifacts") or []
        if artifacts:
            lines.append(
                "<p><b>Result files</b></p><ul>"
                + "".join(
                    f"<li>{escape(str(item.get('relative_path') or item.get('name') or 'artifact'))} · {item.get('size') or 0} bytes</li>"
                    for item in artifacts
                )
                + "</ul>"
            )
        candidate = review.get("candidate_result") or {}
        if candidate.get("text") is not None:
            lines.append(f"<p><b>Current result preview</b></p><pre>{escape(str(candidate.get('text')))}</pre>")
        self.title.setText(f"Review · {review_id[:12]}")
        self.detail.setHtml("".join(lines))
        self._set_action_state(str(data.get("status")) in {"pending_human", "needs_human", "delivery_failed"})

    def _select_item(self, item, _previous) -> None:
        if item:
            self._current_id = str(item.data(256))
            self.select_review_requested.emit(self._current_id)

    def _set_action_state(self, enabled: bool) -> None:
        self.confirm.setEnabled(enabled)
        self.rerun.setEnabled(enabled)
        self.reject.setEnabled(enabled)

    def _emit_confirm(self) -> None:
        if self._current_id:
            self.confirm_requested.emit(self._current_id)

    def _emit_rerun(self) -> None:
        if self._current_id and self.comment.toPlainText().strip():
            self.rerun_requested.emit(self._current_id, self.comment.toPlainText().strip())

    def _emit_reject(self) -> None:
        if self._current_id and self.comment.toPlainText().strip():
            self.reject_requested.emit(self._current_id, self.comment.toPlainText().strip())
