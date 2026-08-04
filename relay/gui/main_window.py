from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..compatibility import evaluate_compatibility
from .agent_apps import AgentAppWizard
from .job_detail import TaskRunDetailView
from .new_task import NewTaskView
from .projects import ProjectRunMonitorDialog, ProjectsView
from .routines import RoutinesView
from .rpc_client import GuiRpcClient
from .schedule_detail import ScheduleDetailView
from .schedule_editor import ScheduleEditorDialog
from .settings import SettingsView
from .state import GuiState
from .tasks import SaveRunAsTaskDialog, TasksView


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
        self.job_input_lookups: dict[str, dict] = {}
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
        self.full_access_states: dict[str, bool] = {}
        self.agent_app_wizard: AgentAppWizard | None = None
        self.agent_app_wizard_mode = "create"
        self.agent_app_wizard_id: str | None = None
        self.current_mode = "disconnected"
        self.current_filter = ""
        self.finished_cursor: str | None = None
        self.job_tree_expanded: dict[str, bool] = {}
        self.selected_job_id: str | None = None
        self.current_detail: dict | None = None
        self.detail_view_mode = "empty"
        self.log_attempt_id: int | None = None
        self.log_offset: int | None = None
        self.progress_check_job_id: str | None = None
        self.health_check_request_id: int | None = None
        self.tasks_index: dict[str, dict] = {}
        self.selected_task_id: str | None = None
        self.task_editor: SaveRunAsTaskDialog | None = None

        self.projects_index: dict[str, dict] = {}
        self.selected_project_id: str | None = None
        self.project_editor = None
        self.project_run_dialog = None

        self.routines_index: dict[str, dict] = {}
        self.selected_routine_id: str | None = None
        self.routine_editor = None

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
        self.top_bar = QFrame()
        self.top_bar.setObjectName("topBar")
        header_layout = QVBoxLayout(self.top_bar)
        title_row = QFrame()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        self.brand_label = QLabel("<b>Relay</b>")
        title_layout.addWidget(self.brand_label)
        self.page_title_label = QLabel("Runs")
        self.page_title_label.setObjectName("pageTitle")
        title_layout.addWidget(self.page_title_label)
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
        self.new_task_button.setObjectName("primaryAction")
        self.new_task_button.clicked.connect(self._show_new_task)
        title_layout.addWidget(self.new_task_button)
        header_layout.addWidget(title_row)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.hide()
        header_layout.addWidget(self.banner)
        outer.addWidget(self.top_bar)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebarNav")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tasks, names, agents...")
        self.search.textChanged.connect(self._on_filter_changed)
        sidebar_layout.addWidget(self.search)
        self.result_filter = self._combo("Result", ["All", "Completed", "Partial", "Failed", "Cancelled"])
        self.agent_filter = self._combo("Agent", ["All", "Claude", "Codex", "Antigravity"])
        self.source_filter = self._combo("Source", ["All", "Command line", "GUI", "Hermes", "Schedule"])
        self.date_filter = self._combo("Date", ["Any time", "Today", "Last 7 days", "Last 30 days"])
        filter_row = QHBoxLayout()
        for combo in (self.result_filter, self.agent_filter, self.source_filter, self.date_filter):
            filter_row.addWidget(combo, 1)
        sidebar_layout.addLayout(filter_row)
        for combo in (self.result_filter, self.agent_filter, self.source_filter, self.date_filter):
            combo.currentIndexChanged.connect(self._on_filter_changed)
        sidebar_layout.addWidget(QLabel("<b>Schedules</b>"))
        self.schedule_list = QListWidget()
        self.schedule_list.setMaximumHeight(150)
        self.schedule_list.itemClicked.connect(self._select_schedule)
        sidebar_layout.addWidget(self.schedule_list)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("sidebarButton")
        self.settings_button.setCheckable(True)
        self.settings_button.clicked.connect(self._show_settings)
        sidebar_layout.addWidget(self.settings_button)
        self.runs_button = QPushButton("Runs")
        self.runs_button.setObjectName("sidebarButton")
        self.runs_button.setCheckable(True)
        self.runs_button.clicked.connect(self._show_runs)
        sidebar_layout.addWidget(self.runs_button)
        self.tasks_button = QPushButton("Tasks")
        self.tasks_button.setObjectName("sidebarButton")
        self.tasks_button.setCheckable(True)
        self.tasks_button.clicked.connect(self._show_tasks)
        sidebar_layout.addWidget(self.tasks_button)
        self.projects_button = QPushButton("Projects")
        self.projects_button.setObjectName("sidebarButton")
        self.projects_button.setCheckable(True)
        self.projects_button.clicked.connect(self._show_projects)
        sidebar_layout.addWidget(self.projects_button)
        self.routines_button = QPushButton("Routines")
        self.routines_button.setObjectName("sidebarButton")
        self.routines_button.setCheckable(True)
        self.routines_button.clicked.connect(self._show_routines)
        sidebar_layout.addWidget(self.routines_button)
        self.job_list = QTreeWidget()
        self.job_list.setHeaderLabels(["Task", "Status"])
        self.job_list.setColumnWidth(0, 210)
        self.job_list.setRootIsDecorated(True)
        self.job_list.setAlternatingRowColors(True)
        self.job_list.itemClicked.connect(self._select_item)
        self.job_list.itemExpanded.connect(lambda item: self._remember_job_tree_state(item, True))
        self.job_list.itemCollapsed.connect(lambda item: self._remember_job_tree_state(item, False))
        sidebar_layout.addWidget(self.job_list, 1)
        self.load_more = QPushButton("Load more")
        self.load_more.clicked.connect(self._load_more_finished)
        self.load_more.setEnabled(False)
        sidebar_layout.addWidget(self.load_more)
        self.splitter.addWidget(self.sidebar)

        self.detail_stack = QStackedWidget()
        self.empty_detail = QLabel("Select a Task Run to view its overview.")
        self.empty_detail.setAlignment(Qt.AlignCenter)
        self.detail_stack.addWidget(self.empty_detail)
        self.new_task_view = NewTaskView()
        self.new_task_view.create_requested.connect(self._create_task)
        self.new_task_view.job_files_requested.connect(self._add_files_from_job)
        self.detail_stack.addWidget(self.new_task_view)
        self.job_detail_view = TaskRunDetailView()
        self.job_detail_view.cancel_requested.connect(self._cancel_job)
        self.job_detail_view.check_requested.connect(self._check_job)
        self.job_detail_view.rerun_requested.connect(self._rerun_job)
        self.job_detail_view.schedule_requested.connect(self._schedule_job)
        self.job_detail_view.tab_requested.connect(self._detail_tab_requested)
        self.job_detail_view.save_as_task_requested.connect(self._open_save_run_as_task)
        self.job_detail_view.open_folder_requested.connect(self._open_folder)
        self.job_detail_view.open_log_requested.connect(self._open_log)
        self.job_detail_view.log_options_changed.connect(self._log_options_changed)
        self.detail_stack.addWidget(self.job_detail_view)
        self.tasks_view = TasksView()
        self.tasks_view.refresh_requested.connect(self._refresh_tasks)
        self.tasks_view.create_requested.connect(self._open_task_editor_for_create)
        self.tasks_view.select_task_requested.connect(self._select_task)
        self.tasks_view.edit_task_requested.connect(self._open_task_editor_for_edit)
        self.tasks_view.delete_task_requested.connect(self._delete_task_requested)
        self.tasks_view.run_task_requested.connect(self._open_task_runner)
        self.tasks_view.task_create_submitted.connect(self._submit_create_task)
        self.tasks_view.task_edit_submitted.connect(self._submit_update_task)
        self.tasks_view.task_run_submitted.connect(self._submit_run_task)
        self.detail_stack.addWidget(self.tasks_view)
        self.projects_view = ProjectsView()
        self.projects_view.refresh_requested.connect(self._refresh_projects)
        self.projects_view.create_requested.connect(self._open_project_editor_for_create)
        self.projects_view.select_project_requested.connect(self._select_project)
        self.projects_view.edit_project_requested.connect(self._open_project_editor_for_edit)
        self.projects_view.delete_project_requested.connect(self._delete_project_requested)
        self.projects_view.run_project_requested.connect(self._submit_create_project_run)
        self.projects_view.project_create_submitted.connect(self._submit_create_project)
        self.projects_view.project_edit_submitted.connect(self._submit_update_project)
        self.projects_view.project_run_submitted.connect(self._submit_project_run_action)
        self.detail_stack.addWidget(self.projects_view)
        self.routines_view = RoutinesView()
        self.routines_view.refresh_requested.connect(self._refresh_routines)
        self.routines_view.create_requested.connect(self._open_routine_editor_for_create)
        self.routines_view.select_routine_requested.connect(self._select_routine)
        self.routines_view.edit_routine_requested.connect(self._open_routine_editor_for_edit)
        self.routines_view.delete_routine_requested.connect(self._delete_routine_requested)
        self.routines_view.run_routine_requested.connect(self._run_routine_now)
        self.routines_view.detail.child_run_requested.connect(self._open_routine_child_run)
        self.routines_view.routine_create_submitted.connect(self._submit_create_routine)
        self.routines_view.routine_edit_submitted.connect(self._submit_update_routine)
        self.routines_view.routine_run_submitted.connect(self._submit_run_routine)
        self.detail_stack.addWidget(self.routines_view)
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
        self.settings_view.full_access_mode_changed.connect(self._set_full_access_mode)
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
        self._activate_navigation("runs")

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
        self.selected_job_id = None
        self.current_detail = None
        self.detail_view_mode = "settings"
        self._activate_navigation("settings")

        self.detail_stack.setCurrentWidget(self.settings_view)
        if self.current_mode == "normal":
            self._request("autostart", "/v1/autostart")
            self._request("security", "/v1/security")
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

    def _set_full_access_mode(self, worker: str, state: bool) -> None:
        if self.current_mode != "normal":
            self.settings_view.set_full_access_state(worker, not state)
            return
        previous = self.full_access_states.get(worker, not state)
        if state:
            choice = QMessageBox.warning(
                self,
                "Enable Full Access Mode",
                f"{worker.title()} Full Access Mode disables that worker's permission checks and sandbox restrictions.\n\n"
                "The worker can modify anything available to the Relay OS account. Use a dedicated low-privilege "
                "account and trusted workspace. This setting applies to the running daemon immediately.\n\n"
                "Enable it only if you understand and accept this risk.",
                QMessageBox.Cancel | QMessageBox.Yes,
                QMessageBox.Cancel,
            )
            if choice != QMessageBox.Yes:
                self.settings_view.set_full_access_state(worker, previous)
                return
        self._request_patch(("full_access", worker, previous), f"/v1/security/full-access/{worker}", {"enabled": state})

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
        self.selected_job_id = None
        self.current_detail = None
        self.detail_view_mode = "new_task"
        self._activate_navigation(None)
        self.detail_stack.setCurrentWidget(self.new_task_view)

    def _create_task(self, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        self._request_post("create", "/v1/jobs", payload)

    # ----- Registered Tasks (Phase 3) ---------------------------------------

    def _show_runs(self) -> None:
        """Return to the Task Run browse context without changing selection."""
        self.detail_view_mode = "runs"
        self._activate_navigation("runs")
        self.empty_detail.setText("Select a Task Run from the Runs list to inspect its evidence.")
        self.detail_stack.setCurrentWidget(self.empty_detail)

    def _activate_navigation(self, section: str | None) -> None:
        buttons = {
            "runs": self.runs_button,
            "tasks": self.tasks_button,
            "projects": self.projects_button,
            "routines": self.routines_button,
            "settings": self.settings_button,
        }
        for name, button in buttons.items():
            button.setChecked(name == section)
        if section:
            self.page_title_label.setText(section.title())

    def _show_tasks(self) -> None:
        self.selected_job_id = None
        self.selected_schedule_id = None
        self.current_detail = None
        self.detail_view_mode = "tasks"
        self._activate_navigation("tasks")
        self.detail_stack.setCurrentWidget(self.tasks_view)
        self.tasks_view.set_available_workers(
            [str(agent.get("agent_id")) for agent in self.agent_definitions if agent.get("agent_id")]
        )
        if self.current_mode == "normal":
            self._refresh_tasks()

    def _refresh_tasks(self) -> None:
        if self.current_mode != "normal":
            return
        self._request("tasks", "/v1/tasks")

    def _select_task(self, task_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.selected_task_id = task_id
        self._request(("task_detail", task_id), f"/v1/tasks/{task_id}")
        self._request(("task_runs", task_id), f"/v1/tasks/{task_id}/runs?limit=20")

    def _open_task_editor_for_create(self) -> None:
        if self.current_mode != "normal":
            return
        self.tasks_view.show_create_editor()

    def _open_task_editor_for_edit(self, task_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.tasks_view.show_edit_editor(task_id)

    def _delete_task_requested(self, task_id: str) -> None:
        if self.current_mode != "normal":
            return
        task = self.tasks_index.get(task_id) or {}
        choice = QMessageBox.warning(
            self,
            "Delete Task",
            f"Delete the registered Task '{(task.get('name') or task_id)}'? Historical Runs stay intact.",
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        self._request_delete(("task_delete", task_id), f"/v1/tasks/{task_id}")

    def _open_task_runner(self, task_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.tasks_view.show_run_dialog(task_id)

    def _submit_create_task(self, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        self._request_post("task_create", "/v1/tasks", payload)

    def _submit_update_task(self, task_id: str, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        self._request_post(("task_update", task_id), f"/v1/tasks/{task_id}", payload)

    def _submit_run_task(self, task_id: str, overrides: dict) -> None:
        if self.current_mode != "normal":
            return
        payload = {"queued": True, "submitted_via": "gui", **overrides}
        self._request_post(("task_run", task_id), f"/v1/tasks/{task_id}/run", payload)

    def _open_save_run_as_task(self) -> None:
        if self.current_mode != "normal" or not self.current_detail:
            return
        run_id = str(self.current_detail.get("job_id") or "")
        if not run_id:
            return
        if self.task_editor is not None:
            self.task_editor.close()
        self.task_editor = SaveRunAsTaskDialog(run_id=run_id, parent=self)
        self.task_editor.accepted_payload.connect(
            lambda payload, rid=run_id: self._submit_save_run_as_task(rid, payload)
        )
        self.task_editor.open()

    def _submit_save_run_as_task(self, run_id: str, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        body = {"run_id": run_id, **payload}
        self._request_post(("save_as_task", run_id), "/v1/runs/save-as-task", body)

    # ----- Registered Projects (Phase 4) ------------------------------------

    def _show_projects(self) -> None:
        self.selected_job_id = None
        self.selected_schedule_id = None
        self.selected_task_id = None
        self.current_detail = None
        self.detail_view_mode = "projects"
        self._activate_navigation("projects")
        self.detail_stack.setCurrentWidget(self.projects_view)
        if self.current_mode == "normal":
            self._refresh_projects()
            self.projects_view.set_tasks(list(self.tasks_index.values()))

    def _refresh_projects(self) -> None:
        if self.current_mode != "normal":
            return
        self._request("projects", "/v1/projects")
        self._request("project_tasks", "/v1/tasks?limit=200")

    def _select_project(self, project_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.selected_project_id = project_id
        self._request(("project_detail", project_id), f"/v1/projects/{project_id}")
        self._request(("project_runs", project_id), f"/v1/projects/{project_id}/runs")

    def _open_project_editor_for_create(self) -> None:
        if self.current_mode != "normal":
            return
        self.projects_view.show_create_editor()

    def _open_project_editor_for_edit(self, project_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.projects_view.show_edit_editor(project_id)

    def _delete_project_requested(self, project_id: str) -> None:
        if self.current_mode != "normal":
            return
        project = self.projects_index.get(project_id) or {}
        choice = QMessageBox.warning(
            self,
            "Delete Project",
            f"Delete the registered Project '{(project.get('name') or project_id)}'? "
            "Project Runs and child Artifacts remain intact.",
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        self._request_delete(("project_delete", project_id), f"/v1/projects/{project_id}")

    def _submit_create_project(self, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        self._request_post("project_create", "/v1/projects", payload)

    def _submit_update_project(self, project_id: str, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        self._request_post(("project_update", project_id), f"/v1/projects/{project_id}", payload)

    def _submit_create_project_run(self, project_id: str) -> None:
        if self.current_mode != "normal":
            return
        self._request_post(("project_run_create", project_id), f"/v1/projects/{project_id}/run", {})

    def _submit_project_run_action(self, project_run_id: str, payload: dict) -> None:
        if self.current_mode != "normal":
            return
        action = payload.get("action")
        if action == "cancel":
            self._request_post(
                ("project_run_action", ("project_run_cancel", project_run_id)),
                f"/v1/project-runs/{project_run_id}/cancel",
                {},
            )
            return
        if action == "retry":
            self._request_post(
                ("project_run_action", ("project_run_retry", project_run_id)),
                f"/v1/project-runs/{project_run_id}/retry",
                {"from_node": payload.get("from_node")},
            )
            return
        if action == "partial-reexecute":
            self._request_post(
                ("project_run_action", ("project_run_reexec", project_run_id)),
                f"/v1/project-runs/{project_run_id}/partial-reexecute",
                {"from_node": payload.get("from_node"), "cascade": bool(payload.get("cascade", True))},
            )
            return
        if action == "refresh":
            self._request(("project_run_steps", project_run_id), f"/v1/project-runs/{project_run_id}/steps")
            return
        self.banner.setText(f"Unknown Project Run action: {action}")
        self.banner.show()

    # ----- Registered Routines (Phase 5) ------------------------------------

    def _show_routines(self) -> None:
        self.selected_job_id = None
        self.selected_schedule_id = None
        self.selected_task_id = None
        self.selected_project_id = None
        self.current_detail = None
        self.detail_view_mode = "routines"
        self._activate_navigation("routines")
        self.detail_stack.setCurrentWidget(self.routines_view)
        self.routines_view.set_tasks(list(self.tasks_index.values()))
        self.routines_view.set_projects(list(self.projects_index.values()))
        if self.current_mode == "normal":
            self._refresh_routines()

    def _refresh_routines(self) -> None:
        if self.current_mode != "normal":
            return
        self._request("routines", "/v1/routines?limit=200")
        self._request("routine_tasks", "/v1/tasks?limit=200")
        self._request("routine_projects", "/v1/projects?limit=200")

    def _select_routine(self, routine_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.selected_routine_id = str(routine_id)
        self._request(("routine_detail", self.selected_routine_id), f"/v1/routines/{routine_id}")
        self._request(("routine_runs", self.selected_routine_id), f"/v1/routines/{routine_id}/runs?limit=100")
        self._request(("routine_receipt", self.selected_routine_id), f"/v1/routines/{routine_id}/receipt")

    def _open_routine_editor_for_create(self) -> None:
        if self.current_mode != "normal":
            return
        self.routines_view.show_create_editor()
        self.routines_view.editor.preview_requested.connect(
            lambda payload, editor=self.routines_view.editor: self._preview_routine(editor, payload)
        )

    def _open_routine_editor_for_edit(self, routine_id: str) -> None:
        if self.current_mode != "normal":
            return
        self.routines_view.show_edit_editor(routine_id)
        self.routines_view.editor.preview_requested.connect(
            lambda payload, editor=self.routines_view.editor: self._preview_routine(editor, payload)
        )

    def _delete_routine_requested(self, routine_id: str) -> None:
        if self.current_mode != "normal":
            return
        routine = self.routines_index.get(routine_id) or {}
        choice = QMessageBox.warning(
            self,
            "Delete Routine",
            f"Delete the Routine '{routine.get('name') or routine_id}'? Historical Runs remain intact.",
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Cancel,
        )
        if choice == QMessageBox.Yes:
            self._request_delete(("routine_delete", routine_id), f"/v1/routines/{routine_id}")

    def _submit_create_routine(self, payload: dict) -> None:
        if self.current_mode == "normal":
            self._request_post("routine_create", "/v1/routines", payload)

    def _submit_update_routine(self, routine_id: str, payload: dict) -> None:
        if self.current_mode == "normal":
            self._request_post(("routine_update", routine_id), f"/v1/routines/{routine_id}", payload)

    def _run_routine_now(self, routine_id: str) -> None:
        self._submit_run_routine(routine_id)

    def _submit_run_routine(self, routine_id: str) -> None:
        if self.current_mode == "normal":
            self._request_post(("routine_run", routine_id), f"/v1/routines/{routine_id}/run-now", {})

    def _preview_routine(self, editor, payload: dict) -> None:
        if self.current_mode == "normal":
            self._request_post(("routine_preview", editor), "/v1/routines/preview", payload)

    def _open_routine_child_run(self, child_type: str, run_id: str) -> None:
        if self.current_mode != "normal":
            return
        if child_type == "task":
            self.selected_job_id = run_id
            self.detail_view_mode = "job"
            self.detail_stack.setCurrentWidget(self.job_detail_view)
            self._request(("detail", run_id), f"/v1/jobs/{run_id}")
            return
        self.project_run_dialog = ProjectRunMonitorDialog(project_run_id=run_id, parent=self)
        self.project_run_dialog.accepted_action.connect(
            lambda action, payload: self._submit_project_run_action(
                payload.get("project_run_id", run_id), {"action": action, **payload}
            )
        )
        self.project_run_dialog.open()
        self._request(("project_run_detail", run_id), f"/v1/project-runs/{run_id}")
        self._request(("project_run_steps", run_id), f"/v1/project-runs/{run_id}/steps")

    def _add_files_from_job(self, job_id: str) -> None:
        if self.current_mode != "normal" or self.job_input_lookups:
            return
        self.job_input_lookups[job_id] = {"responses": {}, "errors": []}
        self.new_task_view.set_job_file_lookup_pending(True)
        encoded_job_id = quote(job_id, safe="")
        self._request(("job_input", job_id, "result"), f"/v1/jobs/{encoded_job_id}/result")
        self._request(("job_input", job_id, "artifacts"), f"/v1/jobs/{encoded_job_id}/artifacts")

    def _record_job_input_response(self, kind: tuple, payload: dict | None, error=None) -> None:
        _, job_id, source = kind
        lookup = self.job_input_lookups.get(job_id)
        if lookup is None:
            return
        lookup["responses"][source] = payload or {}
        if error:
            lookup["errors"].append(str(error))
        if {"result", "artifacts"} - set(lookup["responses"]):
            return

        self.job_input_lookups.pop(job_id, None)
        self.new_task_view.set_job_file_lookup_pending(False)
        responses = lookup["responses"]
        files = self._job_input_candidates(responses.get("result") or {}, responses.get("artifacts") or {})
        if not files:
            self.banner.setText(
                f"No delivered result or artifact files are available for Task Run {job_id}. "
                "Check the Task Run ID and make sure the Task Run has finished."
            )
            self.banner.show()
            return
        if self.detail_view_mode != "new_task":
            self.banner.setText(f"Task Run {job_id} files are ready. Return to New Task and add them again.")
            self.banner.show()
            return
        self.banner.hide()
        self.new_task_view.choose_job_files(job_id, files)

    @staticmethod
    def _job_input_candidates(result: dict, artifacts: dict) -> list[dict]:
        candidates: list[dict] = []
        seen: set[str] = set()

        def append_candidate(
            kind: str,
            value: str | None,
            *,
            name: str | None = None,
            size=None,
            artifact: dict | None = None,
        ) -> None:
            if not value:
                return
            path = Path(value)
            if not path.is_file():
                return
            key = os.path.normcase(str(path.resolve()))
            if key in seen:
                return
            seen.add(key)
            candidates.append(
                {
                    "kind": kind,
                    "name": name or path.name,
                    "path": str(path),
                    "size": path.stat().st_size if size is None else size,
                    **(
                        {
                            "artifact_uid": artifact.get("artifact_uid"),
                            "role": artifact.get("role"),
                            "sha256": artifact.get("sha256"),
                            "source_job_id": artifact.get("job_id"),
                        }
                        if artifact
                        else {}
                    ),
                }
            )

        if result.get("available"):
            append_candidate("Result", result.get("path"), size=result.get("size"))
        for artifact in artifacts.get("artifacts") or []:
            append_candidate(
                "Artifact",
                artifact.get("final_path"),
                name=artifact.get("relative_path"),
                size=artifact.get("size"),
                artifact=artifact,
            )
        return candidates

    @staticmethod
    def _job_artifact_candidates(artifacts: dict) -> list[dict]:
        return [item for item in MainWindow._job_input_candidates({}, artifacts) if item.get("artifact_uid")]

    @staticmethod
    def _artifact_inputs_from_candidates(candidates: list[dict]) -> list[dict]:
        return [
            {"artifact_uid": item["artifact_uid"], "alias": f"A{index}"}
            for index, item in enumerate(candidates, start=1)
            if item.get("artifact_uid")
        ]

    def _cancel_job(self, job_id: str) -> None:
        if self.current_mode == "normal":
            self._request_post("cancel", f"/v1/jobs/{job_id}/cancel", {})

    def _check_job(self, job_id: str) -> None:
        if self.current_mode != "normal" or self.progress_check_job_id is not None:
            return
        self.progress_check_job_id = job_id
        self.job_detail_view.set_check_pending(True)
        self.job_detail_view.select_check_results()
        self._show_check_events([], pending=True)
        self._request_post(("progress_check", job_id), f"/v1/jobs/{job_id}/check", {})

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
            query["search_backend"] = "fts"
            return "/v1/search/runs?" + urlencode({"q": self.search.text().strip(), "limit": "50"})
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
            elif isinstance(kind, tuple) and kind[0] == "routine_preview":
                kind[1].set_preview_error(str(error or "Invalid routine rule"))
            elif isinstance(kind, tuple) and kind[0] == "agent_app_manifest_test":
                kind[1].set_test_result(
                    {"status": "failed", "error": str(error or "Agent test failed")},
                    test_token=None,
                    tested_payload=kind[2],
                )
            elif kind == "antigravity_activate":
                message = (payload or {}).get("error_message") if isinstance(payload, dict) else None
                self.settings_view.set_antigravity_error(message or str(error or "Activation failed"))
            elif isinstance(kind, tuple) and kind[0] == "full_access":
                self.settings_view.set_full_access_state(kind[1], bool(kind[2]))
                message = (payload or {}).get("message") if isinstance(payload, dict) else None
                self.banner.setText(message or str(error or "Full Access Mode could not be updated."))
                self.banner.show()
            elif isinstance(kind, tuple) and kind[0] == "progress_check":
                if self.progress_check_job_id == kind[1]:
                    self.progress_check_job_id = None
                if self.selected_job_id == kind[1] and self.detail_view_mode == "job":
                    self.job_detail_view.set_check_pending(False)
                    self.job_detail_view.select_check_results()
                    self.job_detail_view.set_content(
                        "Logs",
                        f"<pre>{escape('Check failed: ' + str(error or 'Progress diagnosis failed'))}</pre>",
                    )
            elif isinstance(kind, tuple) and kind[0] == "job_input":
                self._record_job_input_response(kind, payload if isinstance(payload, dict) else None, error or True)
            else:
                self.banner.setText("Relay could not complete that action. Please try again.")
                self.banner.show()
            return
        if isinstance(kind, tuple) and kind[0] == "job_input":
            self._record_job_input_response(kind, payload)
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
            self.tasks_view.set_available_workers(
                [str(agent.get("agent_id")) for agent in self.agent_definitions if agent.get("agent_id")]
            )
            return
        if kind in {"autostart", "autostart_prompt", "autostart_toggle"}:
            self.autostart_status = payload.get("autostart") or {}
            self.settings_view.set_autostart_status(self.autostart_status)
            if kind == "autostart_prompt":
                self._maybe_prompt_autostart()
            return
        if kind == "security":
            self.full_access_states = {
                str(worker): bool(enabled) for worker, enabled in (payload.get("full_access_mode") or {}).items()
            }
            self.settings_view.set_full_access_states(
                self.full_access_states.get("codex", False),
                self.full_access_states.get("claude", False),
                self.full_access_states.get("antigravity", False),
            )
            return
        if isinstance(kind, tuple) and kind[0] == "full_access":
            settings = payload.get("full_access_mode") or {}
            self.full_access_states = {str(worker): bool(enabled) for worker, enabled in settings.items()}
            self.settings_view.set_full_access_states(
                self.full_access_states.get("codex", False),
                self.full_access_states.get("claude", False),
                self.full_access_states.get("antigravity", False),
            )
            self.banner.setText(f"{kind[1].title()} Full Access Mode updated for the running daemon.")
            self.banner.show()
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
            if self.detail_view_mode == "job" and self.selected_job_id == kind[1]:
                self._show_detail(payload)
            return
        if isinstance(kind, tuple) and kind[0] == "result":
            if self.detail_view_mode == "job" and self.selected_job_id == kind[1]:
                self.job_detail_view.set_content("Result", self._format_payload(payload))
                data = payload.get("data")
                self.job_detail_view.set_answer(data.get("answer") if isinstance(data, dict) else None)
            return
        if isinstance(kind, tuple) and kind[0] == "progress_check":
            if self.progress_check_job_id == kind[1]:
                self.progress_check_job_id = None
            if self.selected_job_id == kind[1] and self.detail_view_mode == "job":
                self.job_detail_view.set_check_pending(False)
                self.job_detail_view.select_check_results()
                self._request(("check_events", kind[1]), f"/v1/jobs/{kind[1]}/events")
            return
        if kind == "lineage":
            self.job_detail_view.set_content("Inputs", self._format_payload(payload.get("inputs", [])))
            return
        if isinstance(kind, tuple) and kind[0] == "check_events":
            if self.selected_job_id == kind[1] and self.detail_view_mode == "job":
                self._show_check_events(
                    payload.get("events", []),
                    pending=self.progress_check_job_id == kind[1],
                )
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
            if schedule_id and self.detail_view_mode == "schedule":
                self.schedules[schedule_id] = schedule
                self._show_schedule_detail(schedule_id)
            return
        if kind == "schedule_runs":
            schedule_id = payload.get("schedule_id")
            if schedule_id and self.detail_view_mode == "schedule":
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
        if kind == "tasks":
            tasks = payload.get("tasks", [])
            self.tasks_index = {str(t.get("task_id")): t for t in tasks if t.get("task_id")}
            self.tasks_view.set_tasks(tasks)
            if self.selected_task_id and self.selected_task_id not in self.tasks_index:
                self.selected_task_id = None
            if self.selected_task_id and self.selected_task_id in self.tasks_index:
                self.tasks_view.set_task(self.tasks_index[self.selected_task_id])
            self.banner.setText(f"Registered Tasks refreshed · {len(tasks)} entries.")
            self.banner.show()
            return
        if isinstance(kind, tuple) and kind[0] == "task_detail":
            task = (payload or {}).get("task") or {}
            task_id = str(task.get("task_id") or kind[1] or "")
            if task_id and self.selected_task_id == task_id:
                self.tasks_index[task_id] = task
                self.tasks_view.set_task(task)
            return
        if isinstance(kind, tuple) and kind[0] == "task_runs":
            task_id = str(kind[1] or "")
            runs = (payload or {}).get("runs", [])
            if task_id and self.selected_task_id == task_id:
                self.tasks_view.set_runs(task_id, runs)
            return
        if kind == "task_create":
            new_task = (payload or {}).get("task") or {}
            task_id = str(new_task.get("task_id") or "")
            if task_id:
                self.selected_task_id = task_id
                self.tasks_index[task_id] = new_task
                self._request("tasks", "/v1/tasks")
            return
        if isinstance(kind, tuple) and kind[0] == "task_update":
            task_id = str(kind[1] or "")
            if task_id:
                self.selected_task_id = task_id
                updated_task = (payload or {}).get("task") or {}
                if updated_task:
                    self.tasks_index[task_id] = updated_task
                self._request(("task_detail", task_id), f"/v1/tasks/{task_id}")
            return
        if isinstance(kind, tuple) and kind[0] == "task_delete":
            task_id = str(kind[1] or "")
            if task_id:
                self.tasks_index.pop(task_id, None)
                self.selected_task_id = None
                self.tasks_view.detail.clear()
                self._request("tasks", "/v1/tasks")
            return
        if isinstance(kind, tuple) and kind[0] == "task_run":
            new_run = (payload or {}).get("run") or {}
            job_id = new_run.get("task_run_id") or new_run.get("job_id") or new_run.get("run_id")
            if job_id:
                self.selected_job_id = str(job_id)
                self.detail_view_mode = "job"
                self._request(("detail", self.selected_job_id), f"/v1/jobs/{self.selected_job_id}")
            self._refresh_active()
            self._refresh_finished()
            self.banner.setText("Registered Task Run submitted.")
            self.banner.show()
            return
        if kind == "projects":
            projects = payload.get("projects", [])
            self.projects_index = {str(p.get("project_id")): p for p in projects if p.get("project_id")}
            self.projects_view.set_projects(projects)
            if self.selected_project_id and self.selected_project_id not in self.projects_index:
                self.selected_project_id = None
            if self.selected_project_id and self.selected_project_id in self.projects_index:
                self.projects_view.set_project(self.projects_index[self.selected_project_id])
            self.banner.setText(f"Projects refreshed - {len(projects)} entries.")
            self.banner.show()
            return
        if kind == "project_tasks":
            tasks = (payload or {}).get("tasks", [])
            self.tasks_index = {str(t.get("task_id")): t for t in tasks if t.get("task_id")}
            self.projects_view.set_tasks(list(self.tasks_index.values()))
            self.banner.setText(f"Project Tasks refreshed - {len(tasks)} entries.")
            self.banner.show()
            return
        if isinstance(kind, tuple) and kind[0] == "project_detail":
            project = (payload or {}).get("project") or {}
            project_id = str(project.get("project_id") or kind[1] or "")
            if project_id and self.selected_project_id == project_id:
                self.projects_index[project_id] = project
                self.projects_view.set_project(project)
            return
        if isinstance(kind, tuple) and kind[0] == "project_runs":
            project_id = str(kind[1] or "")
            runs = (payload or {}).get("project_runs", [])
            if project_id and self.selected_project_id == project_id:
                self.projects_view.set_runs(project_id, runs)
            return
        if kind == "project_create":
            new_project = (payload or {}).get("project") or {}
            project_id = str(new_project.get("project_id") or "")
            if project_id:
                self.selected_project_id = project_id
                self.projects_index[project_id] = new_project
                self._request("projects", "/v1/projects")
            return
        if isinstance(kind, tuple) and kind[0] == "project_update":
            project_id = str(kind[1] or "")
            updated = (payload or {}).get("project") or {}
            if project_id and updated:
                self.projects_index[project_id] = updated
                self.selected_project_id = project_id
            self._request("projects", "/v1/projects")
            return
        if isinstance(kind, tuple) and kind[0] == "project_delete":
            project_id = str(kind[1] or "")
            if project_id:
                self.projects_index.pop(project_id, None)
                if self.selected_project_id == project_id:
                    self.selected_project_id = None
                    self.projects_view.detail.clear()
                self._request("projects", "/v1/projects")
            return
        if isinstance(kind, tuple) and kind[0] == "project_run_create":
            run_payload = (payload or {}).get("project_run") or {}
            project_run_id = (payload or {}).get("project_run_id") or str(run_payload.get("project_run_id") or "")
            if project_run_id:
                self.banner.setText(f"Project Run {project_run_id[:8]} accepted.")
                self.banner.show()
                self._request("projects", "/v1/projects")
            return
        if isinstance(kind, tuple) and kind[0] == "project_run_action":
            subkind = kind[1][0] if isinstance(kind[1], tuple) else None
            banner_msg = {
                "project_run_cancel": "Project Run cancel requested.",
                "project_run_retry": "Project Run retry requested.",
                "project_run_reexec": "Partial re-execute requested.",
            }.get(subkind, "Project Run action queued.")
            self.banner.setText(banner_msg)
            self.banner.show()
            return

        if kind == "routines":
            routines = payload.get("routines", [])
            self.routines_index = {str(r.get("routine_id")): r for r in routines if r.get("routine_id")}
            self.routines_view.set_routines(routines)
            if self.selected_routine_id and self.selected_routine_id in self.routines_index:
                self.routines_view.set_routine(self.routines_index[self.selected_routine_id])
            elif self.selected_routine_id:
                self.selected_routine_id = None
                self.routines_view.detail.clear()
            self.banner.setText(f"Routines refreshed · {len(routines)} entries.")
            self.banner.show()
            return
        if kind == "routine_tasks":
            tasks = payload.get("tasks", [])
            self.routines_view.set_tasks(tasks)
            return
        if kind == "routine_projects":
            self.routines_view.set_projects(payload.get("projects", []))
            return
        if isinstance(kind, tuple) and kind[0] == "routine_detail":
            routine = (payload or {}).get("routine") or {}
            routine_id = str(routine.get("routine_id") or kind[1] or "")
            if routine_id and self.selected_routine_id == routine_id:
                self.routines_index[routine_id] = routine
                self.routines_view.set_routine(routine)
            return
        if isinstance(kind, tuple) and kind[0] == "routine_runs":
            routine_id = str(kind[1] or "")
            if routine_id and self.selected_routine_id == routine_id:
                self.routines_view.set_runs(routine_id, (payload or {}).get("runs", []))
            return
        if isinstance(kind, tuple) and kind[0] == "routine_receipt":
            routine_id = str(kind[1] or "")
            if routine_id and self.selected_routine_id == routine_id:
                self.routines_view.detail.set_receipt((payload or {}).get("receipt"))
            return
        if isinstance(kind, tuple) and kind[0] == "routine_preview":
            kind[1].set_preview((payload or {}).get("items", []))
            return
        if kind == "routine_create":
            routine = (payload or {}).get("routine") or {}
            routine_id = str(routine.get("routine_id") or "")
            if routine_id:
                self.selected_routine_id = routine_id
                self.routines_index[routine_id] = routine
                self.routines_view.set_routine(routine)
                self._refresh_routines()
            return
        if isinstance(kind, tuple) and kind[0] == "routine_update":
            routine_id = str(kind[1] or "")
            routine = (payload or {}).get("routine") or {}
            if routine_id and routine:
                self.routines_index[routine_id] = routine
                self.selected_routine_id = routine_id
                self.routines_view.set_routine(routine)
            self._refresh_routines()
            return
        if isinstance(kind, tuple) and kind[0] == "routine_delete":
            routine_id = str(kind[1] or "")
            self.routines_index.pop(routine_id, None)
            if self.selected_routine_id == routine_id:
                self.selected_routine_id = None
                self.routines_view.detail.clear()
            self._refresh_routines()
            return
        if isinstance(kind, tuple) and kind[0] == "routine_run":
            self.banner.setText("Routine run accepted.")
            self.banner.show()
            if self.selected_routine_id == str(kind[1]):
                self._select_routine(str(kind[1]))
            return
        if isinstance(kind, tuple) and kind[0] == "project_run_detail":
            if self.project_run_dialog and self.project_run_dialog.project_run_id == str(kind[1]):
                self.project_run_dialog.set_project_run((payload or {}).get("project_run") or {})
            return
        if isinstance(kind, tuple) and kind[0] == "project_run_steps":
            if self.project_run_dialog and self.project_run_dialog.project_run_id == str(kind[1]):
                self.project_run_dialog.set_steps((payload or {}).get("steps", []))
            return

        if isinstance(kind, tuple) and kind[0] == "save_as_task":
            new_task = (payload or {}).get("task") or {}
            task_id = str(new_task.get("task_id") or "")
            self.banner.setText(
                f"Promoted to registered Task '{new_task.get('name') or task_id}'."
                if task_id
                else "Saved Run as Task failed."
            )
            self.banner.show()
            if task_id:
                self._request("tasks", "/v1/tasks")
            return
        if kind in {"create", "cancel", "rerun"}:
            job_id = payload.get("task_run_id") or payload.get("job_id")
            if job_id:
                self.selected_job_id = job_id
                self.detail_view_mode = "job"
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
        for job in payload.get("jobs", payload.get("items", [])):
            if job.get("task_run_id") or job.get("job_id") or job.get("run_id"):
                job_id = job.get("task_run_id") or job.get("job_id") or job.get("run_id")
                self.jobs[job_id] = job
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
            worker_health = (health or {}).get("worker_health") or {}
            if worker_health.get("status") == "unhealthy":
                unhealthy = ", ".join(str(item.get("agent_id")) for item in worker_health.get("unhealthy", []))
                label = f"Unhealthy: {unhealthy or 'engine'}"
            elif worker_health.get("status") == "no-active-engines":
                label = "Health: No active engines"
            else:
                label = "Health: Healthy"
            badge_text = label if worker_health.get("status") == "unhealthy" or not warning else "Health: Attention"
            self._set_health_badge(
                badge_text,
                "#FEE2E2" if worker_health.get("status") == "unhealthy" else "#FEF3C7" if warning else "#DCFCE7",
                "#991B1B" if worker_health.get("status") == "unhealthy" else "#92400E" if warning else "#166534",
                warning or self._health_tooltip(health),
            )
        elif mode == "read-only":
            self._set_health_badge("Health: Compatibility warning", "#FEF3C7", "#92400E", reason)
        else:
            self._set_health_badge("Health: Disconnected", "#FEE2E2", "#991B1B", reason)
        self.new_task_button.setEnabled(mode == "normal")
        self.new_task_view.create_button.setEnabled(mode == "normal")
        self.new_task_view.set_job_file_lookup_enabled(mode == "normal")
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
            "border: 1px solid rgba(0,0,0,0.12); border-radius: 10px; padding: 5px 11px; "
            "font-size: 12px; font-weight: 800; }"
        )
        self.daemon_label.setToolTip(tooltip or text)

    @staticmethod
    def _health_warning(health: dict | None) -> str | None:
        if not health:
            return None
        worker_health = health.get("worker_health") or {}
        if worker_health.get("status") == "unhealthy":
            details = [
                f"{item.get('agent_id')}: {item.get('code') or 'unhealthy'}"
                for item in worker_health.get("unhealthy", [])
            ]
            return "Unhealthy engines: " + ", ".join(details)
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
        selected = self.job_list.currentItem().data(0, Qt.UserRole) if self.job_list.currentItem() else None
        expanded = dict(self.job_tree_expanded)
        for index in range(self.job_list.topLevelItemCount()):
            group = self.job_list.topLevelItem(index)
            state_key = group.data(0, Qt.UserRole + 1)
            if state_key:
                expanded[str(state_key)] = group.isExpanded()
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                state_key = child.data(0, Qt.UserRole + 1)
                if state_key:
                    expanded[str(state_key)] = child.isExpanded()
        self.job_tree_expanded = expanded
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
            group_key = f"group:{group_name}"
            header = QTreeWidgetItem([f"{group_name} · {len(rows)}", ""])
            header.setData(0, Qt.UserRole + 1, group_key)
            header.setFlags(Qt.ItemIsEnabled)
            self.job_list.addTopLevelItem(header)
            date_groups = {"All": rows}
            if group_name == "Finished":
                date_groups = {}
                for job in rows:
                    date_groups.setdefault(self._local_date(job.get(date_key) or job.get("created_at")), []).append(job)
            for date_name, date_rows in date_groups.items():
                if group_name == "Finished":
                    date_key = f"date:{group_name}:{date_name}"
                    date_item = QTreeWidgetItem([f"{date_name} · {len(date_rows)}", ""])
                    date_item.setData(0, Qt.UserRole + 1, date_key)
                    date_item.setFlags(Qt.ItemIsEnabled)
                    header.addChild(date_item)
                for job in date_rows:
                    title = job.get("title") or job.get("job_id", "Task Run")[:8]
                    status = str(job.get("status") or "UNKNOWN")
                    status_text = {
                        "COMPLETED": "Okay",
                        "PARTIAL": "Partial",
                        "FAILED": "Fail",
                        "CANCELLED": "Cancelled",
                        "QUEUED": "Queued",
                    }.get(status, status.title())
                    item = QTreeWidgetItem([str(title), status_text])
                    item.setData(0, Qt.UserRole, job.get("job_id"))
                    item.setToolTip(0, job.get("task_preview") or job.get("job_id", ""))
                    item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    colors = {
                        "COMPLETED": ("#166534", "#F0FDF4"),
                        "PARTIAL": ("#92400E", "#FFFBEB"),
                        "FAILED": ("#991B1B", "#FEF2F2"),
                        "CANCELLED": ("#475569", "#F8FAFC"),
                    }
                    if status in colors:
                        foreground, background = colors[status]
                        for column in range(2):
                            item.setForeground(column, QColor(foreground))
                            item.setBackground(column, QColor(background))
                    (date_item if group_name == "Finished" else header).addChild(item)
                    if job.get("job_id") == selected:
                        self.job_list.setCurrentItem(item)
                if group_name == "Finished":
                    date_item.setExpanded(expanded.get(date_key, True))
            header.setExpanded(expanded.get(group_key, True))

    def _remember_job_tree_state(self, item: QTreeWidgetItem, expanded: bool) -> None:
        state_key = item.data(0, Qt.UserRole + 1)
        if state_key:
            self.job_tree_expanded[str(state_key)] = expanded

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
            self.detail_view_mode = "schedule"
            self._activate_navigation("runs")
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
        self.detail_view_mode = "schedule"
        self._activate_navigation("runs")
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
        if query and job.get("task_preview") and query not in haystack.casefold():
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

    def _select_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        job_id = item.data(0, Qt.UserRole)
        if not job_id:
            return
        self.selected_job_id = job_id
        self.detail_view_mode = "job"
        self._show_detail(self.jobs.get(job_id, {}))
        if self.current_mode == "normal":
            self._request(("detail", job_id), f"/v1/jobs/{job_id}")

    def _show_detail(self, job: dict) -> None:
        if not job or not job.get("job_id"):
            self.detail_view_mode = "empty"
            self.detail_stack.setCurrentWidget(self.empty_detail)
            return
        if self.progress_check_job_id and self.progress_check_job_id != job.get("job_id"):
            self.progress_check_job_id = None
        self.detail_view_mode = "job"
        self._activate_navigation("runs")
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
        paths = {
            "Files": ("artifacts", "artifacts"),
            "Events": ("events", "events"),
            "Inputs": ("lineage", "lineage"),
        }
        if tab_name in paths:
            kind, path = paths[tab_name]
            self._request(kind, f"/v1/jobs/{job_id}/{path}")
        elif tab_name == "Logs":
            if self.job_detail_view.is_check_stream():
                self._request(("check_events", job_id), f"/v1/jobs/{job_id}/events")
                return
            attempts = self.current_detail.get("attempts") or []
            if attempts:
                selected = self.job_detail_view.attempt_combo.currentData()
                self.log_attempt_id = int(selected if selected is not None else attempts[-1]["attempt_id"])
                self.log_offset = None
                self._refresh_log()

    def _log_options_changed(self) -> None:
        if self.job_detail_view.tabs.tabText(self.job_detail_view.tabs.currentIndex()) == "Logs":
            if self.job_detail_view.is_check_stream():
                if self.current_detail and self.current_detail.get("job_id"):
                    job_id = str(self.current_detail["job_id"])
                    self._request(("check_events", job_id), f"/v1/jobs/{job_id}/events")
                return
            self.log_offset = None
            self._refresh_log()

    def _refresh_log(self) -> None:
        if self.current_mode != "normal" or not self.current_detail or self.log_attempt_id is None:
            return
        if self.job_detail_view.tabs.tabText(self.job_detail_view.tabs.currentIndex()) != "Logs":
            return
        if self.job_detail_view.is_check_stream():
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

    def _show_check_events(self, events: list[dict], *, pending: bool = False) -> None:
        records: list[str] = []
        for event in events:
            if event.get("event_type") != "PROGRESS_CHECKED":
                continue
            payload = event.get("payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if not isinstance(payload, dict):
                continue
            timestamp = str(payload.get("checked_at") or event.get("timestamp") or "Unknown time")
            lines = [
                f"[{timestamp}] CHECK — {payload.get('headline') or 'Progress checked'}",
                str(payload.get("summary") or ""),
                " | ".join(
                    item
                    for item in (
                        f"Stage: {payload.get('stage')}" if payload.get("stage") else "",
                        f"Agent: {payload.get('worker')}" if payload.get("worker") else "",
                        (
                            f"Process alive: {'yes' if payload.get('process_alive') else 'no'}"
                            if payload.get("process_alive") is not None
                            else ""
                        ),
                        (
                            f"Elapsed: {self._duration_text(payload.get('elapsed_seconds'))}"
                            if payload.get("elapsed_seconds") is not None
                            else ""
                        ),
                        (
                            f"Idle: {self._duration_text(payload.get('idle_seconds'))}"
                            if payload.get("idle_seconds") is not None
                            else ""
                        ),
                    )
                    if item
                ),
            ]
            activity = payload.get("recent_activity") or {}
            if activity.get("kind"):
                lines.append(f"Recent activity: {activity['kind']}")
            if activity.get("recent_line"):
                lines.append(f"Recent {activity.get('recent_stream') or 'output'}: {activity['recent_line']}")
            issue = payload.get("detected_issue") or {}
            if issue.get("code"):
                lines.append(f"Detected issue: {issue['code']} — {issue.get('message') or ''}")
            records.append("\n".join(line for line in lines if line))
        if pending:
            records.append("[Checking…] Relay is inspecting the current process, activity, and logs.")
        text = "\n\n".join(records) if records else "No progress checks have been recorded for this Task Run."
        self.job_detail_view.set_content("Logs", f"<pre>{escape(text)}</pre>")

    @staticmethod
    def _duration_text(value) -> str:
        seconds = max(0, int(float(value)))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

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
