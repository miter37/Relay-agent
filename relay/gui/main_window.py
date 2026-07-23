from __future__ import annotations

import json
from datetime import datetime, timedelta
from html import escape
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..compatibility import evaluate_compatibility
from .job_detail import JobDetailView
from .new_task import NewTaskView
from .rpc_client import GuiRpcClient
from .state import GuiState


class MainWindow(QMainWindow):
    def __init__(self, config, *, gui_version: str, expected_home_id: str):
        super().__init__()
        self.config = config
        self.gui_version = gui_version
        self.expected_home_id = expected_home_id
        self.state = GuiState(config)
        self.client = GuiRpcClient(config)
        self.client.response.connect(self._handle_response)
        self.pending: dict[int, str] = {}
        self.jobs: dict[str, dict] = {}
        self.current_mode = "disconnected"
        self.current_filter = ""
        self.finished_cursor: str | None = None
        self.selected_job_id: str | None = None
        self.current_detail: dict | None = None
        self.log_attempt_id: int | None = None
        self.log_offset: int | None = None

        self.setWindowTitle("Relay-agent")
        self.resize(1280, 720)
        self._build_ui()
        self._restore_state()

        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self._refresh_health)
        self.health_timer.start(5000)
        self.active_timer = QTimer(self)
        self.active_timer.timeout.connect(self._refresh_active)
        self.active_timer.start(1000)
        self.finished_timer = QTimer(self)
        self.finished_timer.timeout.connect(self._refresh_finished)
        self.finished_timer.start(3000)
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._refresh_log)
        self.log_timer.start(1000)
        self._refresh_health()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        header = QFrame()
        header_layout = QVBoxLayout(header)
        title_row = QFrame()
        title_layout = QVBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.addWidget(QLabel("<b>Relay-agent</b>"))
        self.daemon_label = QLabel("Daemon: Connecting")
        title_layout.addWidget(self.daemon_label)
        self.new_task_button = QPushButton("+ New Task")
        self.new_task_button.clicked.connect(self._show_new_task)
        title_layout.addWidget(self.new_task_button)
        header_layout.addWidget(title_row)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.hide()
        header_layout.addWidget(self.banner)
        outer.addWidget(header)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search finished jobs...")
        self.search.textChanged.connect(self._on_filter_changed)
        sidebar_layout.addWidget(self.search)
        self.result_filter = self._combo("Result", ["All", "Completed", "Partial", "Failed", "Cancelled"])
        self.agent_filter = self._combo("Agent", ["All", "Claude", "Codex", "Antigravity"])
        self.source_filter = self._combo("Source", ["All", "Command line", "GUI", "Hermes", "Schedule"])
        self.date_filter = self._combo("Date", ["Any time", "Today", "Last 7 days", "Last 30 days"])
        sidebar_layout.addWidget(self.result_filter)
        sidebar_layout.addWidget(self.agent_filter)
        sidebar_layout.addWidget(self.source_filter)
        sidebar_layout.addWidget(self.date_filter)
        for combo in (self.result_filter, self.agent_filter, self.source_filter, self.date_filter):
            combo.currentIndexChanged.connect(self._on_filter_changed)
        self.job_list = QListWidget()
        self.job_list.itemClicked.connect(self._select_item)
        sidebar_layout.addWidget(self.job_list, 1)
        self.load_more = QPushButton("Load more")
        self.load_more.clicked.connect(self._load_more_finished)
        self.load_more.setEnabled(False)
        sidebar_layout.addWidget(self.load_more)
        self.splitter.addWidget(self.sidebar)

        self.detail_stack = QStackedWidget()
        self.empty_detail = QLabel("Select a job to view its overview.")
        self.empty_detail.setAlignment(Qt.AlignCenter)
        self.detail_stack.addWidget(self.empty_detail)
        self.new_task_view = NewTaskView()
        self.new_task_view.create_requested.connect(self._create_task)
        self.detail_stack.addWidget(self.new_task_view)
        self.job_detail_view = JobDetailView()
        self.job_detail_view.cancel_requested.connect(self._cancel_job)
        self.job_detail_view.rerun_requested.connect(self._rerun_job)
        self.job_detail_view.tab_requested.connect(self._detail_tab_requested)
        self.job_detail_view.open_result_requested.connect(self._open_result)
        self.job_detail_view.open_folder_requested.connect(self._open_folder)
        self.job_detail_view.open_log_requested.connect(self._open_log)
        self.job_detail_view.log_options_changed.connect(self._log_options_changed)
        self.detail_stack.addWidget(self.job_detail_view)
        self.splitter.addWidget(self.detail_stack)
        self.splitter.setSizes([320, 960])
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(f"Relay Home: {self.config.home}")
        self._set_connection("disconnected", "waiting for daemon compatibility check")

    @staticmethod
    def _combo(prefix: str, values: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(prefix.lower().replace(" ", "_"))
        combo.addItems(values)
        combo.setToolTip(prefix)
        return combo

    def _restore_state(self) -> None:
        geometry = self.state.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        splitter_state = self.state.value("window/splitter_state")
        if splitter_state:
            self.splitter.restoreState(splitter_state)
        self.search.setText(str(self.state.value("filters/search", "")))

    def closeEvent(self, event) -> None:
        self.state.set_value("window/geometry", self.saveGeometry())
        self.state.set_value("window/splitter_state", self.splitter.saveState())
        self.state.set_value("filters/search", self.search.text())
        super().closeEvent(event)

    def _request(self, kind: str, path: str) -> None:
        self.pending[self.client.get(path)] = kind

    def _request_post(self, kind: str, path: str, payload: dict) -> None:
        self.pending[self.client.post(path, payload)] = kind

    def _show_new_task(self) -> None:
        self.detail_stack.setCurrentWidget(self.new_task_view)

    def _create_task(self, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        self._request_post("create", "/v1/jobs", payload)

    def _cancel_job(self, job_id: str) -> None:
        if self.current_mode == "normal":
            self._request_post("cancel", f"/v1/jobs/{job_id}/cancel", {})

    def _rerun_job(self, job_id: str) -> None:
        if self.current_mode == "normal":
            self._request_post("rerun", f"/v1/jobs/{job_id}/rerun", {})

    def _refresh_health(self) -> None:
        self._request("health", "/health")

    def _refresh_active(self) -> None:
        if self.current_mode == "normal":
            self._request("waiting", "/v1/jobs?bucket=waiting&limit=200")
            self._request("running", "/v1/jobs?bucket=running&limit=200")

    def _refresh_finished(self) -> None:
        if self.current_mode == "normal":
            self.finished_cursor = None
            self._request("finished", self._finished_path())

    def _load_more_finished(self) -> None:
        if self.current_mode == "normal" and self.finished_cursor:
            self._request("finished_more", self._finished_path(cursor=self.finished_cursor))

    def _finished_path(self, *, cursor: str | None = None) -> str:
        query: dict[str, str] = {"bucket": "finished", "limit": "50"}
        if self.search.text().strip():
            query["q"] = self.search.text().strip()
        if self.result_filter.currentIndex():
            query["result"] = self.result_filter.currentText().lower()
        if self.agent_filter.currentIndex():
            query["agent"] = self.agent_filter.currentText().lower()
        if self.source_filter.currentIndex():
            query["source"] = {"Command line": "cli", "GUI": "gui", "Hermes": "hermes", "Schedule": "schedule"}[
                self.source_filter.currentText()
            ]
        now = datetime.now().astimezone()
        date_choice = self.date_filter.currentText()
        if date_choice != "Any time":
            days = {"Today": 0, "Last 7 days": 7, "Last 30 days": 30}[date_choice]
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
            query["from"] = start.astimezone().isoformat()
            query["to"] = now.astimezone().isoformat()
        if cursor:
            query["cursor"] = cursor
        return "/v1/jobs?" + urlencode(query)

    def _on_filter_changed(self) -> None:
        if self.current_mode == "normal":
            self.finished_cursor = None
            self._refresh_finished()

    def _handle_response(self, request_id: int, payload, error) -> None:
        kind = self.pending.pop(request_id, None)
        if kind is None:
            return
        if error or not isinstance(payload, dict):
            if kind == "health":
                self._set_connection("disconnected", "Relay daemon is unavailable. Retrying...")
            else:
                self.banner.setText("Relay could not complete that action. Please try again.")
                self.banner.show()
            return
        if kind == "health":
            decision = evaluate_compatibility(
                payload,
                gui_version=self.gui_version,
                expected_relay_home_id=self.expected_home_id,
            )
            self._set_connection(decision.mode, decision.reason)
            if decision.mode == "normal":
                self._request("agents", "/v1/agents")
                self._refresh_active()
                self._refresh_finished()
            return
        if kind == "agents":
            self._update_agent_choices(payload.get("agents", []))
            return
        if kind == "detail":
            self._show_detail(payload)
            return
        if kind == "result":
            self.job_detail_view.set_content("Result", self._format_payload(payload))
            return
        if kind == "artifacts":
            self.job_detail_view.set_content("Files", self._format_payload(payload.get("artifacts", [])))
            return
        if kind == "events":
            self.job_detail_view.set_content("Events", self._format_payload(payload.get("events", [])))
            return
        if kind == "logs":
            self.log_offset = payload.get("next_offset")
            self.job_detail_view.set_content("Logs", f"<pre>{escape(str(payload.get('text') or ''))}</pre>")
            return
        if kind in {"create", "cancel", "rerun"}:
            job_id = payload.get("job_id")
            if job_id:
                self.selected_job_id = job_id
                self._request("detail", f"/v1/jobs/{job_id}")
            self._refresh_active()
            self._refresh_finished()
            if kind == "create":
                self.new_task_view.clear()
            return
        if kind == "finished":
            self._remove_statuses({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"})
        elif kind == "finished_more":
            pass
        else:
            self._remove_statuses(
                {
                    "CREATED",
                    "QUEUED",
                    "PREPARING",
                    "RUNNING",
                    "VALIDATING",
                    "DELIVERING",
                    "CANCEL_REQUESTED",
                }
                if kind in {"waiting", "running"}
                else set()
            )
        for job in payload.get("jobs", []):
            if job.get("job_id"):
                self.jobs[job["job_id"]] = job
        if kind in {"finished", "finished_more"}:
            self.finished_cursor = payload.get("next_cursor")
            self.load_more.setEnabled(bool(payload.get("has_more")))
        self._render_jobs()

    def _remove_statuses(self, statuses: set[str]) -> None:
        for job_id in [job_id for job_id, job in self.jobs.items() if job.get("status") in statuses]:
            del self.jobs[job_id]

    def _set_connection(self, mode: str, reason: str | None = None) -> None:
        self.current_mode = mode
        self.daemon_label.setText("Daemon: Running" if mode == "normal" else "Daemon: Disconnected")
        self.new_task_button.setEnabled(mode == "normal")
        self.new_task_view.create_button.setEnabled(mode == "normal")
        if mode == "normal":
            self.banner.hide()
        else:
            self.banner.setText(f"Read-only compatibility mode: {reason or 'daemon compatibility is unavailable'}")
            self.banner.show()

    def _update_agent_choices(self, agents: list[dict]) -> None:
        current = self.new_task_view.worker_combo.currentText()
        choices = [str(agent.get("agent_id")) for agent in agents if agent.get("agent_id")]
        self.new_task_view.worker_combo.blockSignals(True)
        self.new_task_view.worker_combo.clear()
        self.new_task_view.worker_combo.addItem("auto")
        self.new_task_view.worker_combo.addItems(choices)
        self.new_task_view.worker_combo.setCurrentText(current if current in {"auto", *choices} else "auto")
        self.new_task_view.worker_combo.blockSignals(False)

    def _render_jobs(self) -> None:
        selected = self.job_list.currentItem().data(Qt.UserRole) if self.job_list.currentItem() else None
        self.job_list.clear()
        groups = (
            ("Waiting", {"CREATED", "QUEUED"}, "created_at"),
            ("Running", {"PREPARING", "RUNNING", "VALIDATING", "DELIVERING", "CANCEL_REQUESTED"}, "started_at"),
            ("Finished", {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}, "completed_at"),
        )
        for group_name, statuses, date_key in groups:
            rows = [job for job in self.jobs.values() if job.get("status") in statuses]
            if group_name == "Finished":
                rows = [job for job in rows if self._matches_finished_filters(job)]
            rows.sort(key=lambda job: job.get(date_key) or job.get("created_at") or "", reverse=True)
            if not rows:
                continue
            header = QListWidgetItem(f"▾ {group_name} · {len(rows)}")
            header.setFlags(Qt.ItemIsEnabled)
            self.job_list.addItem(header)
            date_groups = {"All": rows}
            if group_name == "Finished":
                date_groups = {}
                for job in rows:
                    date_groups.setdefault(self._local_date(job.get(date_key) or job.get("created_at")), []).append(job)
            for date_name, date_rows in date_groups.items():
                if group_name == "Finished":
                    date_item = QListWidgetItem(f"▾ {date_name} · {len(date_rows)}")
                    date_item.setFlags(Qt.ItemIsEnabled)
                    self.job_list.addItem(date_item)
                for job in date_rows:
                    title = job.get("title") or job.get("job_id", "Job")[:8]
                    item = QListWidgetItem(f"{self._status_icon(job.get('status'))} {title}")
                    item.setData(Qt.UserRole, job.get("job_id"))
                    item.setToolTip(job.get("task_preview") or job.get("job_id", ""))
                    self.job_list.addItem(item)
                    if job.get("job_id") == selected:
                        self.job_list.setCurrentItem(item)

    @staticmethod
    def _local_date(value: str | None) -> str:
        if not value:
            return "Unknown date"
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%b %d, %Y")
        except ValueError:
            return value[:10]

    def _matches_finished_filters(self, job: dict) -> bool:
        query = self.search.text().strip().casefold()
        haystack = " ".join(
            str(job.get(key) or "") for key in ("title", "task_preview", "job_id", "requested_worker", "actual_worker")
        )
        if query and query not in haystack.casefold():
            return False
        result = self.result_filter.currentText()
        if result != "All" and job.get("status", "").casefold() != result.casefold():
            return False
        agent = self.agent_filter.currentText()
        if agent != "All" and agent.casefold() not in {
            str(job.get("requested_worker") or "").casefold(),
            str(job.get("actual_worker") or "").casefold(),
        }:
            return False
        source = self.source_filter.currentText()
        source_value = {"Command line": "cli", "GUI": "gui", "Hermes": "hermes", "Schedule": "schedule"}.get(
            source, source.casefold()
        )
        if source != "All" and job.get("submitted_via", "").casefold() != source_value:
            return False
        return True

    @staticmethod
    def _status_icon(status: str | None) -> str:
        return {"COMPLETED": "✓", "PARTIAL": "◐", "FAILED": "×", "CANCELLED": "—"}.get(status or "", "●")

    def _select_item(self, item: QListWidgetItem) -> None:
        job_id = item.data(Qt.UserRole)
        if not job_id:
            return
        self.selected_job_id = job_id
        self._show_detail(self.jobs.get(job_id, {}))
        if self.current_mode == "normal":
            self._request("detail", f"/v1/jobs/{job_id}")

    def _show_detail(self, job: dict) -> None:
        if not job or not job.get("job_id"):
            self.detail_stack.setCurrentWidget(self.empty_detail)
            return
        self.current_detail = job
        self.log_attempt_id = None
        self.log_offset = None
        self.job_detail_view.set_job(job)
        self.detail_stack.setCurrentWidget(self.job_detail_view)

    def _detail_tab_requested(self, tab_name: str) -> None:
        if self.current_mode != "normal" or not self.current_detail:
            return
        job_id = self.current_detail.get("job_id")
        if not job_id:
            return
        paths = {"Result": ("result", "result"), "Files": ("artifacts", "artifacts"), "Events": ("events", "events")}
        if tab_name in paths:
            kind, path = paths[tab_name]
            self._request(kind, f"/v1/jobs/{job_id}/{path}")
        elif tab_name == "Logs":
            attempts = self.current_detail.get("attempts") or []
            if attempts:
                selected = self.job_detail_view.attempt_combo.currentData()
                self.log_attempt_id = int(selected if selected is not None else attempts[-1]["attempt_id"])
                self.log_offset = None
                self._refresh_log()

    def _log_options_changed(self) -> None:
        if self.job_detail_view.tabs.tabText(self.job_detail_view.tabs.currentIndex()) == "Logs":
            self.log_offset = None
            self._refresh_log()

    def _refresh_log(self) -> None:
        if self.current_mode != "normal" or not self.current_detail or self.log_attempt_id is None:
            return
        if self.job_detail_view.tabs.tabText(self.job_detail_view.tabs.currentIndex()) != "Logs":
            return
        stream = self.job_detail_view.stream_combo.currentText()
        query = {
            "attempt_id": str(self.log_attempt_id),
            "stream": stream,
            "limit": "16000",
            "errors_only": "1" if self.job_detail_view.errors_only_check.isChecked() else "0",
        }
        if self.log_offset is not None:
            query["offset"] = str(self.log_offset)
        self._request("logs", f"/v1/jobs/{self.current_detail['job_id']}/logs?{urlencode(query)}")

    def _open_result(self, job_id: str) -> None:
        self._open_stored_path(job_id, "output_path", file_only=True)

    def _open_folder(self, job_id: str) -> None:
        self._open_stored_path(job_id, "artifact_path", directory_only=True)

    def _open_log(self, job_id: str) -> None:
        if not self.current_detail or self.current_detail.get("job_id") != job_id:
            return
        attempt_id = self.job_detail_view.attempt_combo.currentData()
        stream = self.job_detail_view.stream_combo.currentText()
        for attempt in self.current_detail.get("attempts") or []:
            if int(attempt.get("attempt_id", -1)) == int(attempt_id):
                self._open_path(attempt.get(f"{stream}_path"), file_only=True)
                return
        self._show_open_error()

    def _open_stored_path(
        self, job_id: str, field: str, *, file_only: bool = False, directory_only: bool = False
    ) -> None:
        if not self.current_detail or self.current_detail.get("job_id") != job_id:
            return
        self._open_path(self.current_detail.get(field), file_only=file_only, directory_only=directory_only)

    def _open_path(self, value: str | None, *, file_only: bool = False, directory_only: bool = False) -> None:
        from pathlib import Path

        path = Path(value) if value else None
        if (
            not path
            or not path.exists()
            or (file_only and not path.is_file())
            or (directory_only and not path.is_dir())
        ):
            self._show_open_error()
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_open_error(self) -> None:
        self.banner.setText("The stored file or folder is no longer available.")
        self.banner.show()

    @staticmethod
    def _format_payload(value) -> str:
        return f"<pre>{escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))}</pre>"
