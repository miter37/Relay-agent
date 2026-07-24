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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..compatibility import evaluate_compatibility
from .agent_apps import AgentAppWizard
from .job_detail import JobDetailView
from .new_task import NewTaskView
from .rpc_client import GuiRpcClient
from .schedule_detail import ScheduleDetailView
from .schedule_editor import ScheduleEditorDialog
from .settings import SettingsView
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
        self.pending: dict[int, object] = {}
        self.jobs: dict[str, dict] = {}
        self.agent_definitions: list[dict] = []
        self.custom_agent_apps: list[dict] = []
        self.schedules: dict[str, dict] = {}
        self.schedule_runs: dict[str, list[dict]] = {}
        self.selected_schedule_id: str | None = None
        self.schedule_editor: ScheduleEditorDialog | None = None
        self.schedule_editor_mode = "create"
        self.schedule_editor_schedule_id: str | None = None
        self.autostart_status: dict = {}
        self.agent_app_wizard: AgentAppWizard | None = None
        self.agent_app_wizard_mode = "create"
        self.agent_app_wizard_id: str | None = None
        self.current_mode = "disconnected"
        self.current_filter = ""
        self.finished_cursor: str | None = None
        self.selected_job_id: str | None = None
        self.current_detail: dict | None = None
        self.log_attempt_id: int | None = None
        self.log_offset: int | None = None
        self.health_check_request_id: int | None = None

        self.setWindowTitle("Relay-agent")
        self.resize(1280, 720)
        self._build_ui()
        self._restore_state()

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
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.addWidget(QLabel("<b>Relay-agent</b>"))
        title_layout.addStretch(1)
        self.health_label = QLabel("Health: Checking…")
        self.daemon_label = self.health_label
        self.health_time_label = QLabel("Not checked")
        self.health_refresh_button = QPushButton("Refresh health")
        self.health_refresh_button.clicked.connect(self._refresh_health)
        title_layout.addWidget(self.health_label)
        title_layout.addWidget(self.health_time_label)
        title_layout.addWidget(self.health_refresh_button)
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
        sidebar_layout.addWidget(QLabel("<b>Schedules</b>"))
        self.schedule_list = QListWidget()
        self.schedule_list.setMaximumHeight(150)
        self.schedule_list.itemClicked.connect(self._select_schedule)
        sidebar_layout.addWidget(self.schedule_list)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self._show_settings)
        sidebar_layout.addWidget(self.settings_button)
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
        self.job_detail_view.schedule_requested.connect(self._schedule_job)
        self.job_detail_view.tab_requested.connect(self._detail_tab_requested)
        self.job_detail_view.open_result_requested.connect(self._open_result)
        self.job_detail_view.open_folder_requested.connect(self._open_folder)
        self.job_detail_view.open_log_requested.connect(self._open_log)
        self.job_detail_view.log_options_changed.connect(self._log_options_changed)
        self.detail_stack.addWidget(self.job_detail_view)
        self.schedule_detail_view = ScheduleDetailView()
        self.schedule_detail_view.run_now_requested.connect(self._run_schedule_now)
        self.schedule_detail_view.pause_requested.connect(self._pause_schedule)
        self.schedule_detail_view.resume_requested.connect(self._resume_schedule)
        self.schedule_detail_view.edit_requested.connect(self._edit_schedule)
        self.schedule_detail_view.copy_requested.connect(self._copy_schedule)
        self.schedule_detail_view.delete_requested.connect(self._delete_schedule)
        self.schedule_detail_view.open_output_requested.connect(self._open_schedule_output)
        self.detail_stack.addWidget(self.schedule_detail_view)
        self.settings_view = SettingsView()
        self.settings_view.autostart_changed.connect(self._toggle_autostart)
        self.settings_view.antigravity_activate_requested.connect(self._activate_antigravity)
        agent_apps = self.settings_view.agent_apps_view
        agent_apps.create_requested.connect(self._create_agent_app)
        agent_apps.edit_requested.connect(self._edit_agent_app)
        agent_apps.test_requested.connect(self._test_agent_app)
        agent_apps.enabled_requested.connect(self._set_agent_app_enabled)
        agent_apps.delete_requested.connect(self._delete_agent_app)
        self.detail_stack.addWidget(self.settings_view)
        self.splitter.addWidget(self.detail_stack)
        self.splitter.setSizes([320, 960])
        outer.addWidget(self.splitter, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage(f"Relay Home: {self.config.home}")
        self._set_connection("checking", "waiting for daemon health check")

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
        for timer in (self.active_timer, self.finished_timer, self.log_timer):
            timer.stop()
        self.client.close()
        self.state.set_value("window/geometry", self.saveGeometry())
        self.state.set_value("window/splitter_state", self.splitter.saveState())
        self.state.set_value("filters/search", self.search.text())
        super().closeEvent(event)

    def _request(self, kind, path: str) -> None:
        self.pending[self.client.get(path)] = kind

    def _show_settings(self) -> None:
        self.detail_stack.setCurrentWidget(self.settings_view)
        if self.current_mode == "normal":
            self._request("autostart", "/v1/autostart")
            self._request("agent_apps", "/v1/agent-apps")
            self._request("antigravity_setup", "/v1/agents/antigravity/setup")

    def _create_agent_app(self) -> None:
        if self.current_mode != "normal":
            return
        self.agent_app_wizard_mode = "create"
        self.agent_app_wizard_id = None
        self._open_agent_app_wizard(AgentAppWizard(self))

    def _edit_agent_app(self, agent_id: str) -> None:
        if self.current_mode == "normal":
            self.agent_app_wizard_mode = "update"
            self.agent_app_wizard_id = agent_id
            self._request(("agent_app_detail", agent_id), f"/v1/agent-apps/{agent_id}")

    def _test_agent_app(self, agent_id: str) -> None:
        if self.current_mode == "normal":
            self._request_post(("agent_app_test", None), f"/v1/agent-apps/{agent_id}/test", {})

    def _set_agent_app_enabled(self, agent_id: str, enabled: bool) -> None:
        if self.current_mode == "normal":
            self._request_patch("agent_app_enabled", f"/v1/agent-apps/{agent_id}/enabled", {"enabled": enabled})

    def _delete_agent_app(self, agent_id: str) -> None:
        if self.current_mode == "normal":
            self._request_delete("agent_app_delete", f"/v1/agent-apps/{agent_id}")

    def _wizard_test_agent_app(self, payload: dict) -> None:
        if self.agent_app_wizard is None:
            return
        self._request_post(
            ("agent_app_manifest_test", self.agent_app_wizard, payload),
            "/v1/agent-apps/test-manifest",
            {"mode": self.agent_app_wizard_mode, "manifest": payload},
        )

    def _save_agent_app(self, payload: dict) -> None:
        if self.current_mode != "normal" or self.agent_app_wizard is None:
            return
        agent_id = self.agent_app_wizard_id or payload.get("agent_id")
        if not agent_id:
            self.banner.setText("Agent ID is required.")
            self.banner.show()
        elif self.agent_app_wizard_mode == "create":
            self._request_post(("agent_app_save", self.agent_app_wizard), "/v1/agent-apps", payload)
        else:
            self._request_patch(
                ("agent_app_save", self.agent_app_wizard),
                f"/v1/agent-apps/{agent_id}",
                payload,
            )

    def _open_agent_app_wizard(self, wizard: AgentAppWizard) -> None:
        if self.agent_app_wizard is not None and self.agent_app_wizard is not wizard:
            self.agent_app_wizard.reject()
        self.agent_app_wizard = wizard
        wizard.test_requested.connect(self._wizard_test_agent_app)
        wizard.save_requested.connect(self._save_agent_app)
        wizard.finished.connect(lambda: self._clear_agent_app_wizard(wizard))
        wizard.open()

    def _clear_agent_app_wizard(self, wizard: AgentAppWizard) -> None:
        if self.agent_app_wizard is wizard:
            self.agent_app_wizard = None
            self.agent_app_wizard_id = None

    def _toggle_autostart(self, enabled: bool) -> None:
        if self.current_mode == "normal":
            self._request_patch("autostart_toggle", "/v1/autostart", {"enabled": enabled})

    def _activate_antigravity(self) -> None:
        if self.current_mode != "normal":
            return
        choice = QMessageBox.warning(
            self,
            "Enable Antigravity",
            "Antigravity runs with permission checks bypassed and can use the full rights of the current OS account.\n\n"
            "Continue only after verifying that Relay runs under a dedicated low-privilege account or equivalent "
            "OS-level isolation. Relay does not create that isolation for you.",
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        self.settings_view.set_antigravity_pending(True)
        self._request_post(
            "antigravity_activate",
            "/v1/agents/antigravity/activate",
            {"isolation_acknowledged": True},
            timeout_ms=310000,
        )

    def _maybe_prompt_autostart(self) -> None:
        if self.autostart_status.get("enabled") or self._state_truthy("gui/autostart_prompted"):
            return
        choice = QMessageBox.question(
            self,
            "Start Relay automatically",
            "Would you like Relay to start the daemon when you sign in?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        self.state.set_value("gui/autostart_prompted", True)
        if choice == QMessageBox.Yes:
            self._request_patch("autostart_toggle", "/v1/autostart", {"enabled": True})

    def _state_truthy(self, key: str) -> bool:
        value = self.state.value(key, False)
        if isinstance(value, str):
            return value.casefold() in {"1", "true", "yes", "on"}
        return bool(value)

    def _request_post(self, kind, path: str, payload: dict, *, timeout_ms: int = 15000) -> None:
        self.pending[self.client.post(path, payload, timeout_ms=timeout_ms)] = kind

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

    def _schedule_job(self, job_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.schedule_editor_mode = "create"
        self.schedule_editor_schedule_id = None
        self.schedule_editor = ScheduleEditorDialog(source_job_id=job_id, parent=self)
        self.schedule_editor.preview_requested.connect(self._schedule_preview)
        self.schedule_editor.save_requested.connect(self._schedule_create)
        self.schedule_editor.open()

    def _edit_schedule(self, schedule_id: str) -> None:
        if self.current_mode != "normal":
            return
        schedule = self.schedules.get(schedule_id)
        if not schedule:
            return
        self.schedule_editor_mode = "update"
        self.schedule_editor_schedule_id = schedule_id
        self.schedule_editor = ScheduleEditorDialog(source_job_id=str(schedule.get("source_job_id") or ""), parent=self)
        self.schedule_editor.setWindowTitle("Edit schedule")
        self.schedule_editor.save_button.setText("Save changes")
        self.schedule_editor.set_schedule(schedule)
        self.schedule_editor.preview_requested.connect(self._schedule_preview)
        self.schedule_editor.save_requested.connect(self._schedule_save)
        self.schedule_editor.open()

    def _schedule_preview(self, payload: dict) -> None:
        if self.schedule_editor is not None:
            self._request_post(("schedule_preview", self.schedule_editor), "/v1/schedules/preview", payload)

    def _schedule_create(self, payload: dict) -> None:
        if self.schedule_editor is None:
            return
        self._schedule_save(payload)

    def _schedule_save(self, payload: dict) -> None:
        if self.schedule_editor is None:
            return
        if self.schedule_editor_mode == "update" and self.schedule_editor_schedule_id:
            self._request_patch(
                ("schedule_update", self.schedule_editor),
                f"/v1/schedules/{self.schedule_editor_schedule_id}",
                payload,
            )
            return
        source_job_id = self.schedule_editor.source_job_id
        self._request_post(
            ("schedule_create", self.schedule_editor),
            f"/v1/schedules/from-job/{source_job_id}",
            payload,
        )

    def _request_patch(self, kind, path: str, payload: dict) -> None:
        self.pending[self.client.patch(path, payload)] = kind

    def _request_delete(self, kind, path: str) -> None:
        self.pending[self.client.delete(path)] = kind

    def _schedule_action(self, kind: str, schedule_id: str, path: str) -> None:
        if self.current_mode == "normal":
            self._request_post((kind, schedule_id), f"/v1/schedules/{schedule_id}/{path}", {})

    def _run_schedule_now(self, schedule_id: str) -> None:
        self._schedule_action("schedule_run_now", schedule_id, "run-now")

    def _pause_schedule(self, schedule_id: str) -> None:
        self._schedule_action("schedule_pause", schedule_id, "pause")

    def _resume_schedule(self, schedule_id: str) -> None:
        self._schedule_action("schedule_resume", schedule_id, "resume")

    def _copy_schedule(self, schedule_id: str) -> None:
        if self.current_mode == "normal":
            schedule = self.schedules.get(schedule_id, {})
            self._request_post(
                ("schedule_copy", schedule_id),
                f"/v1/schedules/{schedule_id}/copy",
                {"name": f"{schedule.get('name') or 'Schedule'} copy"},
            )

    def _delete_schedule(self, schedule_id: str) -> None:
        if self.current_mode == "normal":
            self._request_delete(("schedule_delete", schedule_id), f"/v1/schedules/{schedule_id}")

    def _open_schedule_output(self, schedule_id: str) -> None:
        schedule = self.schedules.get(schedule_id, {})
        self._open_path(schedule.get("output_root"), directory_only=True)

    def _refresh_health(self) -> None:
        if self.health_check_request_id is not None:
            return
        self.health_refresh_button.setEnabled(False)
        self.health_label.setText("Health: Checking…")
        request_id = self.client.get("/health")
        self.health_check_request_id = request_id
        self.pending[request_id] = "health"

    def _refresh_active(self) -> None:
        if self.current_mode == "normal":
            self._request("active", "/v1/jobs?bucket=active&limit=200")

    def _refresh_finished(self) -> None:
        if self.current_mode == "normal":
            self.finished_cursor = None
            self._request("finished", self._finished_path())
            self._request("schedules", "/v1/schedules")

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
                self.health_check_request_id = None
                self.health_refresh_button.setEnabled(True)
                self.health_time_label.setText(f"Failed {datetime.now().astimezone():%H:%M:%S}")
                self._set_connection("disconnected", "Relay daemon is unavailable")
            elif isinstance(kind, tuple) and kind[0] == "schedule_preview":
                kind[1].set_preview_error(str(error or "Invalid schedule"))
            elif isinstance(kind, tuple) and kind[0] == "agent_app_manifest_test":
                kind[1].set_test_result(
                    {"status": "failed", "error": str(error or "Agent test failed")},
                    test_token=None,
                    tested_payload=kind[2],
                )
            elif kind == "antigravity_activate":
                message = (payload or {}).get("error_message") if isinstance(payload, dict) else None
                self.settings_view.set_antigravity_error(message or str(error or "Activation failed"))
            else:
                self.banner.setText("Relay could not complete that action. Please try again.")
                self.banner.show()
            return
        if kind == "health":
            self.health_check_request_id = None
            self.health_refresh_button.setEnabled(True)
            self.health_time_label.setText(f"Checked {datetime.now().astimezone():%H:%M:%S}")
            decision = evaluate_compatibility(
                payload,
                gui_version=self.gui_version,
                expected_relay_home_id=self.expected_home_id,
                supported_schema_revision=5,
            )
            self._set_connection(decision.mode, decision.reason, health=payload)
            if decision.mode == "normal":
                self._request("agents", "/v1/agents")
                self._request("autostart", "/v1/autostart")
                self._request("agent_apps", "/v1/agent-apps")
                self._refresh_active()
                self._refresh_finished()
            return
        if kind == "agents":
            self.agent_definitions = payload.get("agents", [])
            self._update_agent_choices(self.agent_definitions)
            self._render_agent_apps()
            return
        if kind in {"autostart", "autostart_prompt", "autostart_toggle"}:
            self.autostart_status = payload.get("autostart") or {}
            self.settings_view.set_autostart_status(self.autostart_status)
            if kind == "autostart_prompt":
                self._maybe_prompt_autostart()
            return
        if kind == "antigravity_setup":
            self.settings_view.set_antigravity_status(payload.get("antigravity") or {})
            return
        if kind == "antigravity_activate":
            self.settings_view.set_antigravity_pending(False)
            self.settings_view.set_antigravity_status(payload.get("antigravity") or {})
            self._request("agents", "/v1/agents")
            self.banner.setText("Antigravity was verified and enabled.")
            self.banner.show()
            return
        if kind == "agent_apps":
            self.custom_agent_apps = payload.get("agent_apps", [])
            self._render_agent_apps()
            return
        if isinstance(kind, tuple) and kind[0] == "agent_app_detail":
            if self.agent_app_wizard_mode != "update" or self.agent_app_wizard_id != kind[1]:
                return
            agent = payload.get("agent") or {}
            wizard = AgentAppWizard(self)
            wizard.set_agent(agent)
            self._open_agent_app_wizard(wizard)
            return
        if isinstance(kind, tuple) and kind[0] == "detail":
            if self.selected_job_id == kind[1]:
                self._show_detail(payload)
            return
        if isinstance(kind, tuple) and kind[0] == "result":
            if self.selected_job_id == kind[1]:
                self.job_detail_view.set_content("Result", self._format_payload(payload))
                data = payload.get("data")
                self.job_detail_view.set_answer(data.get("answer") if isinstance(data, dict) else None)
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
        if kind == "schedules":
            self.schedules = {
                str(schedule.get("schedule_id")): schedule
                for schedule in payload.get("schedules", [])
                if schedule.get("schedule_id")
            }
            self._render_schedules()
            return
        if kind == "schedule_detail":
            schedule = payload.get("schedule") or {}
            schedule_id = schedule.get("schedule_id")
            if schedule_id:
                self.schedules[schedule_id] = schedule
                self._show_schedule_detail(schedule_id)
            return
        if kind == "schedule_runs":
            schedule_id = payload.get("schedule_id")
            if schedule_id:
                self.schedule_runs[schedule_id] = payload.get("runs", [])
                self._show_schedule_detail(schedule_id)
            return
        if isinstance(kind, tuple) and kind[0] == "schedule_preview":
            kind[1].set_preview(payload.get("occurrences", []))
            return
        if isinstance(kind, tuple) and kind[0] == "agent_app_manifest_test":
            wizard = kind[1]
            if self.agent_app_wizard is wizard:
                wizard.set_test_result(
                    payload.get("test") or {},
                    test_token=payload.get("test_token"),
                    tested_payload=kind[2],
                )
            return
        if isinstance(kind, tuple) and kind[0] == "agent_app_test":
            wizard = kind[1]
            if wizard is not None:
                wizard.set_test_result(
                    payload.get("test") or {},
                    test_token=None,
                    tested_payload=wizard.payload(),
                )
            else:
                self._request("agent_apps", "/v1/agent-apps")
            return
        if isinstance(kind, tuple) and kind[0] == "agent_app_save":
            saved_agent = payload.get("agent") or {}
            if saved_agent.get("status") != "ready":
                kind[1].set_test_result(
                    {"status": "failed", "error": "Saved definition still requires a test."},
                    test_token=None,
                    tested_payload=kind[1].payload(),
                )
                return
            kind[1].accept()
            if not saved_agent.get("enabled"):
                self.banner.setText("Agent saved and tested. Enable it when you are ready to use it.")
                self.banner.show()
            self._request("agent_apps", "/v1/agent-apps")
            return
        if kind in {"agent_app_enabled", "agent_app_delete"}:
            self._request("agent_apps", "/v1/agent-apps")
            return
        if isinstance(kind, tuple) and kind[0] == "schedule_create":
            schedule = payload.get("schedule") or {}
            if schedule.get("schedule_id"):
                self.schedules[schedule["schedule_id"]] = schedule
            kind[1].accept()
            self.schedule_editor = None
            self._render_schedules()
            self._refresh_finished()
            self._request("autostart_prompt", "/v1/autostart")
            return
        if isinstance(kind, tuple) and kind[0] == "schedule_update":
            schedule = payload.get("schedule") or {}
            if schedule.get("schedule_id"):
                self.schedules[schedule["schedule_id"]] = schedule
            kind[1].accept()
            self.schedule_editor = None
            self._render_schedules()
            self._refresh_schedule(self.schedule_editor_schedule_id)
            return
        if isinstance(kind, tuple) and kind[0] in {
            "schedule_pause",
            "schedule_resume",
            "schedule_copy",
            "schedule_delete",
            "schedule_run_now",
        }:
            action, schedule_id = kind
            if action == "schedule_delete":
                self.schedules.pop(schedule_id, None)
                self.schedule_runs.pop(schedule_id, None)
                self.detail_stack.setCurrentWidget(self.empty_detail)
            elif action == "schedule_copy":
                schedule = payload.get("schedule") or {}
                if schedule.get("schedule_id"):
                    self.schedules[schedule["schedule_id"]] = schedule
            elif payload.get("schedule", {}).get("schedule_id"):
                self.schedules[schedule_id] = payload["schedule"]
            self._render_schedules()
            self._refresh_schedule(schedule_id if action != "schedule_copy" else None)
            return
        if kind in {"create", "cancel", "rerun"}:
            job_id = payload.get("job_id")
            if job_id:
                self.selected_job_id = job_id
                self._request(("detail", job_id), f"/v1/jobs/{job_id}")
            self._refresh_active()
            self._refresh_finished()
            if kind == "create":
                self.new_task_view.clear()
            return
        if kind == "finished":
            self._remove_statuses({"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"})
        elif kind == "finished_more":
            pass
        elif kind == "active":
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
            )
        for job in payload.get("jobs", []):
            if job.get("job_id"):
                self.jobs[job["job_id"]] = job
        if kind in {"finished", "finished_more"}:
            self.finished_cursor = payload.get("next_cursor")
            self.load_more.setEnabled(bool(payload.get("has_more")))
        self._render_jobs()
        if kind in {"active", "finished"} and self.selected_job_id:
            self._request(
                ("detail", self.selected_job_id),
                f"/v1/jobs/{self.selected_job_id}",
            )

    def _remove_statuses(self, statuses: set[str]) -> None:
        for job_id in [job_id for job_id, job in self.jobs.items() if job.get("status") in statuses]:
            del self.jobs[job_id]

    def _set_connection(self, mode: str, reason: str | None = None, *, health: dict | None = None) -> None:
        self.current_mode = mode if mode in {"normal", "read-only"} else "disconnected"
        if mode == "checking":
            self._set_health_badge("Health: Checking…", "#FEF3C7", "#92400E", reason)
        elif mode == "normal":
            warning = self._health_warning(health)
            self._set_health_badge(
                "Health: Attention" if warning else "Health: Healthy",
                "#FEF3C7" if warning else "#DCFCE7",
                "#92400E" if warning else "#166534",
                warning or self._health_tooltip(health),
            )
        elif mode == "read-only":
            self._set_health_badge("Health: Compatibility warning", "#FEF3C7", "#92400E", reason)
        else:
            self._set_health_badge("Health: Disconnected", "#FEE2E2", "#991B1B", reason)
        self.new_task_button.setEnabled(mode == "normal")
        self.new_task_view.create_button.setEnabled(mode == "normal")
        self.schedule_list.setEnabled(mode == "normal")
        self.settings_button.setEnabled(mode == "normal")
        if mode == "normal":
            self.banner.hide()
        else:
            self.banner.setText(f"Read-only compatibility mode: {reason or 'daemon compatibility is unavailable'}")
            self.banner.show()

    def _set_health_badge(self, text: str, background: str, foreground: str, tooltip: str | None) -> None:
        self.daemon_label.setText(text)
        self.daemon_label.setStyleSheet(
            f"QLabel {{ background: {background}; color: {foreground}; "
            "border-radius: 10px; padding: 3px 9px; font-weight: 600; }"
        )
        self.daemon_label.setToolTip(tooltip or text)

    @staticmethod
    def _health_warning(health: dict | None) -> str | None:
        if not health:
            return None
        for name in ("cleanup", "schedule_retention"):
            last_report = (health.get(name) or {}).get("last_report")
            if isinstance(last_report, dict) and (last_report.get("ok") is False or last_report.get("errors")):
                return f"{name.replace('_', ' ').capitalize()} reported errors."
        return None

    @staticmethod
    def _health_tooltip(health: dict | None) -> str:
        if not health:
            return "Health details unavailable."
        return (
            f"Daemon {health.get('daemon_version') or 'unknown'} · "
            f"API schema {health.get('api_schema_revision') or 'unknown'} · "
            f"Started {health.get('started_at') or 'unknown'}"
        )

    def _render_agent_apps(self) -> None:
        combined = {
            str(agent["agent_id"]): agent
            for agent in [*self.agent_definitions, *self.custom_agent_apps]
            if agent.get("agent_id")
        }
        self.settings_view.set_agent_apps(list(combined.values()))

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

    def _render_schedules(self) -> None:
        selected = self.schedule_list.currentItem().data(Qt.UserRole) if self.schedule_list.currentItem() else None
        self.schedule_list.clear()
        rows = sorted(self.schedules.values(), key=lambda schedule: str(schedule.get("name") or "").casefold())
        for schedule in rows:
            if schedule.get("needs_attention"):
                icon = "×"
            elif schedule.get("enabled"):
                icon = "●"
            else:
                icon = "○"
            name = schedule.get("name") or schedule.get("schedule_id", "Schedule")[:8]
            state = "paused" if not schedule.get("enabled") else schedule.get("next_run_at_utc") or "active"
            item = QListWidgetItem(f"{icon} {name} · {state}")
            item.setData(Qt.UserRole, schedule.get("schedule_id"))
            item.setToolTip(str(schedule.get("attention_code") or schedule.get("schedule_id") or ""))
            self.schedule_list.addItem(item)
            if schedule.get("schedule_id") == selected:
                self.schedule_list.setCurrentItem(item)

    def _select_schedule(self, item: QListWidgetItem) -> None:
        schedule_id = item.data(Qt.UserRole)
        if schedule_id:
            self.selected_schedule_id = str(schedule_id)
            self._refresh_schedule(self.selected_schedule_id)

    def _refresh_schedule(self, schedule_id: str | None) -> None:
        if self.current_mode != "normal" or not schedule_id:
            return
        self._request("schedule_detail", f"/v1/schedules/{schedule_id}")
        self._request("schedule_runs", f"/v1/schedules/{schedule_id}/runs")

    def _show_schedule_detail(self, schedule_id: str) -> None:
        schedule = self.schedules.get(schedule_id)
        if not schedule:
            return
        self.selected_schedule_id = schedule_id
        self.schedule_detail_view.set_schedule(schedule, self.schedule_runs.get(schedule_id, []))
        self.detail_stack.setCurrentWidget(self.schedule_detail_view)

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
            self._request(("detail", job_id), f"/v1/jobs/{job_id}")

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
        if tab_name in {"Answer", "Result"}:
            self._request(("result", job_id), f"/v1/jobs/{job_id}/result")
            return
        paths = {"Files": ("artifacts", "artifacts"), "Events": ("events", "events")}
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
