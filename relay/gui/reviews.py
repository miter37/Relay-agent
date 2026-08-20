"""Human-friendly inbox for Task and Project result review gates."""

from __future__ import annotations

import json
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

from .artifacts import ArtifactExplorerView, ArtifactGroup, ArtifactRecord
from .scroll_state import preserve_scroll, set_html


class ReviewsView(QWidget):
    select_review_requested = Signal(str)
    confirm_requested = Signal(str)
    rerun_requested = Signal(str, str)
    reject_requested = Signal(str, str)
    refresh_requested = Signal()
    artifact_preview_requested = Signal(str, str)
    open_artifact_requested = Signal(str)
    open_artifact_folder_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._reviews: dict[str, dict] = {}
        self._current_id: str | None = None
        self._comment_drafts: dict[str, str] = {}
        self._review_actionable = False
        self._action_pending = False
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
        self.artifacts_view = ArtifactExplorerView()
        self.artifacts_view.preview_requested.connect(self.artifact_preview_requested.emit)
        self.artifacts_view.open_file_requested.connect(lambda path: self.open_artifact_requested.emit(path))
        self.artifacts_view.open_folder_requested.connect(lambda path: self.open_artifact_folder_requested.emit(path))
        panel.addWidget(self.artifacts_view, 2)
        self.comment = QTextEdit()
        self.comment.setPlaceholderText("Say what should change on the next attempt")
        self.comment.setMaximumHeight(84)
        self.comment.textChanged.connect(self._draft_changed)
        panel.addWidget(self.comment)
        self.comment_hint = QLabel("Add feedback to enable rerun or reject.")
        self.comment_hint.setObjectName("mutedText")
        panel.addWidget(self.comment_hint)
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
        self._set_inbox_chrome(False)

    def set_reviews(self, reviews: list[dict]) -> None:
        self._reviews = {str(item.get("review_id")): item for item in reviews if item.get("review_id")}
        selected = self._current_id
        with preserve_scroll(self.list):
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
            set_html(
                self.detail,
                "<p>When a Task or Project result needs a decision, it will appear in the list.</p>",
            )
            self.artifacts_view.set_groups([], auto_select_primary=False)
            self._review_actionable = False
            self._set_action_state(False)
            self._set_inbox_chrome(False)

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
        evaluation = _decode_evaluation(data.get("evaluation_json"))
        if evaluation:
            decision = _decision_text(evaluation.get("decision"))
            reason = evaluation.get("reason") or evaluation.get("comment") or "No reason recorded."
            lines.append(f"<p><b>Orchestrator evaluation:</b> {escape(decision)}<br>{_review_text(reason)}</p>")
        elif str(data.get("status") or "") == "evaluating":
            lines.append("<p><b>Orchestrator evaluation:</b> In progress…</p>")
        if str(data.get("status") or "") in {"needs_human", "delivery_failed"} and data.get("comment"):
            lines.append(f"<p><b>Human handoff:</b><br>{_review_text(data.get('comment'))}</p>")
        rounds = review.get("rounds") or []
        if rounds:
            round_rows = []
            for round_data in rounds:
                if not isinstance(round_data, dict):
                    continue
                round_eval = _decode_evaluation(round_data.get("evaluation_json"))
                detail = _decision_text(round_eval.get("decision")) if round_eval else round_data.get("comment")
                round_rows.append(
                    f"Round {escape(str(round_data.get('round_no') or '—'))}: "
                    f"{escape(_status_text(round_data.get('status')))} — {escape(str(detail or round_data.get('task_run_id') or '—'))}"
                )
            if round_rows:
                lines.append("<p><b>Round history</b><br>" + "<br>".join(round_rows) + "</p>")
        lines.append("<p><b>Artifacts</b> are shown below. Select one to preview it, inspect its path, or open it.</p>")
        self.title.setText(f"Review · {review_id[:12]}")
        set_html(self.detail, "".join(lines))
        records: list[ArtifactRecord] = []
        for item in review.get("artifacts") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("publication_status") or "candidate")
            records.append(
                ArtifactRecord.from_mapping(
                    item,
                    review_id=review_id,
                    is_primary=status == "candidate",
                )
            )
        candidate = review.get("candidate_result") or {}
        candidate_path = str(candidate.get("path") or "")
        if candidate_path and not any(record.final_path == candidate_path for record in records):
            records.append(
                ArtifactRecord.from_mapping(
                    {
                        "artifact_uid": f"review-result:{review_id}",
                        "role": "result",
                        "relative_path": candidate_path,
                        "final_path": candidate_path,
                        "publication_status": "candidate",
                        "mime_type": "application/json" if candidate_path.casefold().endswith(".json") else "",
                    },
                    review_id=review_id,
                    is_primary=not records,
                )
            )
        self.artifacts_view.set_groups(
            [ArtifactGroup("Review candidate artifacts", tuple(records))] if records else [],
            auto_select_primary=True,
        )
        if candidate_path and self.artifacts_view.selected_record() and candidate.get("text") is not None:
            uid = f"review-result:{review_id}"
            self.artifacts_view.cache_content(
                uid,
                {
                    "available": True,
                    "text": str(candidate.get("text") or ""),
                    "truncated": bool(candidate.get("truncated")),
                },
            )
        self._review_actionable = str(data.get("status")) in {"pending_human", "needs_human", "delivery_failed"}
        self._set_action_state(self._review_actionable)
        self._set_inbox_chrome(True)

    def _select_item(self, item, _previous) -> None:
        if item:
            self._save_current_draft()
            self._current_id = str(item.data(256))
            self._load_current_draft()
            self.select_review_requested.emit(self._current_id)

    def _set_inbox_chrome(self, selected: bool) -> None:
        """Hide decision chrome until a review is actually selected."""
        self.artifacts_view.setVisible(selected)
        self.comment.setVisible(selected)
        self.confirm.setVisible(selected)
        self.rerun.setVisible(selected)
        self.reject.setVisible(selected)
        if not selected:
            self.comment_hint.setVisible(False)

    def _set_action_state(self, enabled: bool) -> None:
        self._review_actionable = enabled
        pending = self._action_pending
        has_comment = bool(self.comment.toPlainText().strip())
        self.confirm.setEnabled(enabled and not pending)
        self.rerun.setEnabled(enabled and has_comment and not pending)
        self.reject.setEnabled(enabled and has_comment and not pending)
        self.comment_hint.setVisible(enabled and not pending and not has_comment)

    def set_action_pending(self, pending: bool) -> None:
        self._action_pending = pending
        self._set_action_state(self._review_actionable)
        self.refresh.setEnabled(not pending)

    def current_review_id(self) -> str | None:
        return self._current_id

    def action_completed(self, review_id: str) -> None:
        if review_id and review_id == self._current_id:
            self._comment_drafts.pop(review_id, None)
            self.comment.blockSignals(True)
            self.comment.clear()
            self.comment.blockSignals(False)
        self.set_action_pending(False)

    def _save_current_draft(self) -> None:
        if self._current_id:
            self._comment_drafts[self._current_id] = self.comment.toPlainText()

    def _load_current_draft(self) -> None:
        self.comment.blockSignals(True)
        self.comment.setPlainText(self._comment_drafts.get(self._current_id or "", ""))
        self.comment.blockSignals(False)
        self._set_action_state(self._review_actionable)

    def _draft_changed(self) -> None:
        self._save_current_draft()
        self._set_action_state(self._review_actionable)

    def _emit_confirm(self) -> None:
        if self._current_id:
            self.set_action_pending(True)
            self.confirm_requested.emit(self._current_id)

    def _emit_rerun(self) -> None:
        if self._current_id and self.comment.toPlainText().strip():
            self.set_action_pending(True)
            self.rerun_requested.emit(self._current_id, self.comment.toPlainText().strip())

    def _emit_reject(self) -> None:
        if self._current_id and self.comment.toPlainText().strip():
            self.set_action_pending(True)
            self.reject_requested.emit(self._current_id, self.comment.toPlainText().strip())


def _decode_evaluation(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decision_text(value: object) -> str:
    return {
        "approve": "Confirmed",
        "rerun": "Rerun requested",
        "human_review": "Human review required",
    }.get(str(value or "").casefold(), str(value or "Evaluation recorded"))


def _status_text(value: object) -> str:
    return str(value or "Unknown").replace("_", " ").title()


def _review_text(value: object) -> str:
    return escape(str(value or "")).replace("\n", "<br>")
