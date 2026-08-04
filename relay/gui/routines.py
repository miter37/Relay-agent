"""Phase 5 registered-Routines GUI widgets."""

from __future__ import annotations

import json
from html import escape

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


def _format_fields(payload):
    if not payload:
        return "<i>No details available.</i>"
    rows = "".join(
        f"<tr><td><b>{escape(str(key))}</b></td><td>{escape(str(value or '-'))}</td></tr>"
        for key, value in payload.items()
    )
    return f"<table>{rows}</table>"


def _parse_json_or_none(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _routine_status_label(routine):
    target = f"{routine.get('target_type', '?')}:{routine.get('target_id', '?')}"
    next_run = routine.get("next_run_at_utc") or "-"
    return f"{target} - next {next_run}"


class RoutinesListView(QWidget):
    select_routine_requested = Signal(str)
    refresh_requested = Signal()
    create_routine_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.routines = []
        self.routines_by_id = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Routines</b>"), 1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("mutedText")
        header.addWidget(self.count_label)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self.refresh_button)
        self.create_button = QPushButton("New Routine")
        self.create_button.clicked.connect(self.create_routine_requested.emit)
        header.addWidget(self.create_button)
        layout.addLayout(header)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter by name")
        self.search_edit.textChanged.connect(self._rerender)
        layout.addWidget(self.search_edit)
        self.list_widget = QListWidget()
        self.list_widget.itemActivated.connect(self._item_activated)
        layout.addWidget(self.list_widget, 1)

    def set_routines(self, routines):
        self.routines = list(routines)
        self.routines_by_id = {str(r.get("routine_id")): r for r in routines if r.get("routine_id")}
        self._rerender()

    def selected_routine_id(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _rerender(self):
        query = self.search_edit.text().strip().casefold()
        self.list_widget.clear()
        visible = 0
        for routine in sorted(self.routines, key=lambda r: str(r.get("name") or "").casefold()):
            name = str(routine.get("name") or routine.get("routine_id") or "Routine")
            if query and query not in name.casefold():
                continue
            enabled = bool(routine.get("enabled"))
            dot = "*" if enabled else "o"
            label = f"{dot} {name} - {_routine_status_label(routine)}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(routine.get("routine_id") or ""))
            self.list_widget.addItem(item)
            visible += 1
        total = len(self.routines)
        if not total:
            self.count_label.setText("No registered Routines")
        elif query and visible != total:
            self.count_label.setText(f"{visible} of {total} routines match")
        elif query:
            self.count_label.setText(f"{total} routines match")
        elif total >= 200:
            self.count_label.setText(f"{total} routines (server may have more)")
        else:
            self.count_label.setText(f"{total} routines")

    def _item_activated(self, item):
        routine_id = item.data(Qt.UserRole)
        if routine_id:
            self.select_routine_requested.emit(str(routine_id))


class RoutineDetailView(QWidget):
    refresh_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    run_requested = Signal(str)
    child_run_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.routine_id = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title_label = QLabel("Routine")
        self.title_label.setObjectName("pageTitle")
        header.addWidget(self.title_label, 1)
        self.status_label = QLabel("")
        header.addWidget(self.status_label)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._on_refresh)
        header.addWidget(self.refresh_button)
        self.run_button = QPushButton("Run now")
        self.run_button.clicked.connect(self._on_run)
        header.addWidget(self.run_button)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self._on_edit)
        header.addWidget(self.edit_button)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._on_delete)
        header.addWidget(self.delete_button)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.overview_browser = QTextBrowser()
        self.definition_browser = QTextBrowser()
        self.runs_browser = QTextBrowser()
        self.runs_browser.setOpenLinks(False)
        self.runs_browser.anchorClicked.connect(self._on_run_link)
        self.receipt_browser = QTextBrowser()
        self.tabs.addTab(self.overview_browser, "Overview")
        self.tabs.addTab(self.definition_browser, "Definition")
        self.tabs.addTab(self.runs_browser, "Runs")
        self.tabs.addTab(self.receipt_browser, "Receipt")
        layout.addWidget(self.tabs, 1)
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_routine(self, routine, runs=None):
        self.routine_id = str(routine.get("routine_id") or "") or None
        self.title_label.setText(str(routine.get("name") or self.routine_id or "Routine"))
        enabled = bool(routine.get("enabled"))
        state = "Enabled" if enabled else "Disabled"
        target = f"{routine.get('target_type', '?')}:{routine.get('target_id', '?')}"
        self.status_label.setText(f"{state} - target {target}")
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(self.routine_id is not None)
        self.overview_browser.setHtml(
            _format_fields(
                {
                    "Routine ID": routine.get("routine_id"),
                    "Name": routine.get("name"),
                    "Target type": routine.get("target_type"),
                    "Target ID": routine.get("target_id"),
                    "Timezone": routine.get("timezone"),
                    "Overlap policy": routine.get("overlap_policy"),
                    "Missed policy": routine.get("missed_policy"),
                    "Missed grace (s)": routine.get("missed_grace_seconds"),
                    "Version policy": routine.get("version_policy"),
                    "Pinned version": routine.get("pinned_version"),
                    "Starts at (UTC)": routine.get("starts_at_utc"),
                    "Ends at (UTC)": routine.get("ends_at_utc"),
                    "Last occurrence": routine.get("last_occurrence_key"),
                    "Next run (UTC)": routine.get("next_run_at_utc"),
                    "Enabled": enabled,
                }
            )
        )
        self.definition_browser.setHtml(_format_fields(routine))
        self.runs_browser.setHtml(self._format_runs(runs or []))
        self.receipt_browser.clear()

    def clear(self):
        self.routine_id = None
        self.title_label.setText("Routine")
        self.status_label.setText("")
        for browser in (self.overview_browser, self.definition_browser, self.runs_browser, self.receipt_browser):
            browser.clear()
        for button in (self.refresh_button, self.run_button, self.edit_button, self.delete_button):
            button.setEnabled(False)

    def set_runs(self, runs):
        self.runs_browser.setHtml(self._format_runs(runs))

    def set_receipt(self, receipt):
        if not receipt:
            self.receipt_browser.setHtml("<i>No receipt available.</i>")
            return
        self.receipt_browser.setHtml(
            f"<pre>{escape(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))}</pre>"
        )

    @staticmethod
    def _format_runs(runs):
        if not runs:
            return "<i>No Routine Runs recorded yet.</i>"
        rows = "".join(
            "<tr>"
            f"<td>{RoutineDetailView._run_link(run)}</td>"
            f"<td>{escape(str(run.get('status') or '-'))}</td>"
            f"<td>{escape(str(run.get('trigger_type') or '-'))}</td>"
            f"<td>{escape(str(run.get('scheduled_for_utc') or run.get('updated_at') or '-'))}</td>"
            "</tr>"
            for run in runs
        )
        return f"<table><tr><th>Run</th><th>Status</th><th>Trigger</th><th>When</th></tr>{rows}</table>"

    @staticmethod
    def _run_link(run):
        run_id = str(run.get("run_id") or "-")
        child_id = run.get("task_run_id") or run.get("project_run_id")
        child_type = "task" if run.get("task_run_id") else "project" if run.get("project_run_id") else None
        if not child_id or not child_type:
            return escape(run_id)
        return f'<a href="relay://{child_type}/{escape(str(child_id))}">{escape(run_id)}</a>'

    def _on_run_link(self, url: QUrl):
        if url.scheme() != "relay" or url.host() not in {"task", "project"}:
            return
        run_id = url.path().lstrip("/")
        if run_id:
            self.child_run_requested.emit(url.host(), run_id)

    def _on_refresh(self):
        if self.routine_id:
            self.refresh_requested.emit(self.routine_id)

    def _on_run(self):
        if self.routine_id:
            self.run_requested.emit(self.routine_id)

    def _on_edit(self):
        if self.routine_id:
            self.edit_requested.emit(self.routine_id)

    def _on_delete(self):
        if self.routine_id:
            self.delete_requested.emit(self.routine_id)


class RoutineEditorDialog(QDialog):
    accepted_payload = Signal(dict)
    preview_requested = Signal(dict)

    _TARGET_TYPES = ("task", "project")
    # queue/cancel_previous are not implemented by the current core runtime.
    _OVERLAP = ("skip", "allow_parallel")
    _MISSED = ("skip", "run_once_on_recovery", "replay_all")
    _VERSION_POLICIES = ("latest", "pinned")

    def __init__(
        self,
        *,
        routine=None,
        available_tasks=None,
        available_projects=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Routine" if routine else "New Routine")
        self.resize(720, 720)
        self._routine_id = str(routine.get("routine_id") or "") if routine else ""
        self._tasks_by_label: dict[str, str] = {}
        self._projects_by_label: dict[str, str] = {}
        self._task_id_by_label: dict[str, str] = {}
        self._project_id_by_label: dict[str, str] = {}

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Name", self.name_edit)

        self.target_type_combo = QComboBox()
        self.target_type_combo.addItems(self._TARGET_TYPES)
        self.target_type_combo.currentTextChanged.connect(self._on_target_type_changed)
        form.addRow("Target type", self.target_type_combo)

        self.target_id_combo = QComboBox()
        self.target_id_combo.setEditable(False)
        form.addRow("Target", self.target_id_combo)

        self.timezone_edit = QLineEdit("UTC")
        form.addRow("Time zone", self.timezone_edit)

        self.overlap_combo = QComboBox()
        self.overlap_combo.addItems(self._OVERLAP)
        form.addRow("Overlap policy", self.overlap_combo)

        self.missed_combo = QComboBox()
        self.missed_combo.addItems(self._MISSED)
        form.addRow("Missed policy", self.missed_combo)

        self.grace_spin = QSpinBox()
        self.grace_spin.setRange(0, 7 * 24 * 60 * 60)
        self.grace_spin.setValue(12 * 60 * 60)
        form.addRow("Missed grace (seconds)", self.grace_spin)

        self.version_policy_combo = QComboBox()
        self.version_policy_combo.addItems(self._VERSION_POLICIES)
        self.version_policy_combo.currentTextChanged.connect(self._on_version_policy_changed)
        form.addRow("Version policy", self.version_policy_combo)

        self.pinned_version_spin = QSpinBox()
        self.pinned_version_spin.setRange(1, 100000)
        self.pinned_version_spin.setEnabled(False)
        form.addRow("Pinned version", self.pinned_version_spin)

        self.starts_at_edit = QLineEdit()
        self.starts_at_edit.setPlaceholderText("Optional ISO datetime")
        form.addRow("Starts at (UTC)", self.starts_at_edit)

        self.ends_at_edit = QLineEdit()
        self.ends_at_edit.setPlaceholderText("Optional ISO datetime")
        form.addRow("Ends at (UTC)", self.ends_at_edit)

        self.enabled_checkbox = QCheckBox("Enabled")
        self.enabled_checkbox.setChecked(True)
        form.addRow("State", self.enabled_checkbox)

        root.addLayout(form)

        root.addWidget(QLabel("<b>Rule (JSON)</b>"))
        self.rule_edit = QTextEdit()
        self.rule_edit.setAcceptRichText(False)
        self.rule_edit.setPlaceholderText('{"type": "daily", "times": ["09:00"], "timezone": "UTC"}')
        self.rule_edit.setMinimumHeight(100)
        root.addWidget(self.rule_edit, 2)

        preview_row = QHBoxLayout()
        self.preview_button = QPushButton("Preview next occurrences")
        self.preview_button.clicked.connect(self._on_preview)
        preview_row.addWidget(self.preview_button)
        preview_row.addStretch(1)
        root.addLayout(preview_row)
        self.preview_browser = QTextBrowser()
        self.preview_browser.setMinimumHeight(70)
        root.addWidget(self.preview_browser, 1)

        root.addWidget(QLabel("<b>Input policy (JSON)</b>"))
        self.input_policy_edit = QTextEdit()
        self.input_policy_edit.setAcceptRichText(False)
        self.input_policy_edit.setMinimumHeight(60)
        root.addWidget(self.input_policy_edit, 1)

        root.addWidget(QLabel("<b>Notification policy (JSON)</b>"))
        self.notification_policy_edit = QTextEdit()
        self.notification_policy_edit.setAcceptRichText(False)
        self.notification_policy_edit.setMinimumHeight(60)
        root.addWidget(self.notification_policy_edit, 1)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("errorText")
        root.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.set_available_choices(tasks=available_tasks or [], projects=available_projects or [])
        if routine:
            self._populate(routine)

    def set_available_choices(self, *, tasks, projects):
        self._tasks_by_label = {
            f"{t.get('name')} ({t.get('task_id')})": str(t.get("task_id")) for t in (tasks or []) if t.get("task_id")
        }
        self._projects_by_label = {
            f"{p.get('name')} ({p.get('project_id')})": str(p.get("project_id"))
            for p in (projects or [])
            if p.get("project_id")
        }
        self._task_id_by_label = {v: k for k, v in self._tasks_by_label.items()}
        self._project_id_by_label = {v: k for k, v in self._projects_by_label.items()}
        self._on_target_type_changed(self.target_type_combo.currentText())

    def _on_target_type_changed(self, target_type):
        self.target_id_combo.clear()
        mapping = self._tasks_by_label if target_type == "task" else self._projects_by_label
        for label in sorted(mapping):
            self.target_id_combo.addItem(label)

    def _on_version_policy_changed(self, policy):
        self.pinned_version_spin.setEnabled(policy == "pinned")

    def show_error(self, message):
        self.error_label.setText(message)

    def _on_preview(self):
        try:
            rule_text = self.rule_edit.toPlainText().strip()
            if not rule_text:
                raise ValueError("Rule JSON is required.")
            rule = json.loads(rule_text)
            if not isinstance(rule, dict):
                raise ValueError("Rule must be a JSON object.")
            timezone = self.timezone_edit.text().strip() or "UTC"
            rule.setdefault("timezone", timezone)
            payload = {"rule": rule, "timezone": timezone, "limit": 5}
            starts_at = self.starts_at_edit.text().strip()
            ends_at = self.ends_at_edit.text().strip()
            if starts_at:
                payload["starts_at_utc"] = starts_at
            if ends_at:
                payload["ends_at_utc"] = ends_at
        except (ValueError, json.JSONDecodeError) as exc:
            self.set_preview_error(str(exc))
            return
        self.preview_requested.emit(payload)

    def set_preview(self, occurrences):
        if not occurrences:
            self.preview_browser.setHtml("<i>No occurrences in the configured active range.</i>")
            return
        rows = "".join(
            f"<li>{escape(str(item.get('local_time') or '-'))} ({escape(str(item.get('instant_utc') or '-'))})</li>"
            for item in occurrences
        )
        self.preview_browser.setHtml(f"<b>Next occurrences</b><ul>{rows}</ul>")

    def set_preview_error(self, message):
        self.preview_browser.setHtml(f"<span style='color:#991B1B'>{escape(str(message))}</span>")

    def _populate(self, routine):
        self.name_edit.setText(str(routine.get("name") or ""))
        target_type = str(routine.get("target_type") or "task")
        if target_type in self._TARGET_TYPES:
            self.target_type_combo.setCurrentText(target_type)
        target_id = str(routine.get("target_id") or "")
        mapping = self._tasks_by_label if target_type == "task" else self._projects_by_label
        target_label = next((label for label, value in mapping.items() if value == target_id), None)
        if target_label:
            self.target_id_combo.setCurrentText(target_label)
        self.timezone_edit.setText(str(routine.get("timezone") or "UTC"))
        if routine.get("overlap_policy") in self._OVERLAP:
            self.overlap_combo.setCurrentText(routine["overlap_policy"])
        if routine.get("missed_policy") in self._MISSED:
            self.missed_combo.setCurrentText(routine["missed_policy"])
        self.grace_spin.setValue(int(routine.get("missed_grace_seconds") or 0))
        version_policy = str(routine.get("version_policy") or "latest")
        if version_policy in self._VERSION_POLICIES:
            self.version_policy_combo.setCurrentText(version_policy)
        self._on_version_policy_changed(version_policy)
        if routine.get("pinned_version") is not None:
            self.pinned_version_spin.setValue(int(routine["pinned_version"]))
        self.starts_at_edit.setText(str(routine.get("starts_at_utc") or ""))
        self.ends_at_edit.setText(str(routine.get("ends_at_utc") or ""))
        self.enabled_checkbox.setChecked(bool(routine.get("enabled", True)))
        rule_json = _parse_json_or_none(routine.get("rule_json")) or routine.get("rule")
        if rule_json is not None:
            self.rule_edit.setPlainText(json.dumps(rule_json, indent=2))
        input_policy = _parse_json_or_none(routine.get("input_policy_json")) or routine.get("input_policy")
        if input_policy is not None:
            self.input_policy_edit.setPlainText(json.dumps(input_policy, indent=2))
        notification_policy = _parse_json_or_none(routine.get("notification_policy_json")) or routine.get(
            "notification_policy"
        )
        if notification_policy is not None:
            self.notification_policy_edit.setPlainText(json.dumps(notification_policy, indent=2))

    def _on_save(self):
        try:
            payload = self.payload()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.accepted_payload.emit(payload)
        self.accept()

    def payload(self):
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Routine name is required.")
        target_type = self.target_type_combo.currentText().strip() or "task"
        if target_type not in self._TARGET_TYPES:
            raise ValueError(f"Unknown target_type: {target_type}")
        target_label = self.target_id_combo.currentText().strip()
        if not target_label:
            raise ValueError("Pick a target.")
        mapping = self._tasks_by_label if target_type == "task" else self._projects_by_label
        if target_label not in mapping:
            raise ValueError(f"Target not in {target_type} list.")
        target_id = mapping[target_label]
        rule_text = self.rule_edit.toPlainText().strip()
        if not rule_text:
            raise ValueError("Rule JSON is required.")
        try:
            rule = json.loads(rule_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Rule must be valid JSON: {exc}") from exc
        if not isinstance(rule, dict):
            raise ValueError("Rule must be a JSON object.")
        rule.setdefault("timezone", self.timezone_edit.text().strip() or "UTC")
        payload = {
            "name": name,
            "target_type": target_type,
            "target_id": target_id,
            "rule": rule,
            "timezone": rule["timezone"],
            "overlap_policy": self.overlap_combo.currentText(),
            "missed_policy": self.missed_combo.currentText(),
            "missed_grace_seconds": int(self.grace_spin.value()),
            "version_policy": self.version_policy_combo.currentText(),
            "enabled": bool(self.enabled_checkbox.isChecked()),
        }
        if payload["version_policy"] == "pinned":
            payload["pinned_version"] = int(self.pinned_version_spin.value())
        starts_at = self.starts_at_edit.text().strip()
        ends_at = self.ends_at_edit.text().strip()
        if starts_at:
            payload["starts_at_utc"] = starts_at
        if ends_at:
            payload["ends_at_utc"] = ends_at
        for key, edit in (
            ("input_policy", self.input_policy_edit),
            ("notification_policy", self.notification_policy_edit),
        ):
            text = edit.toPlainText().strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{key} must be valid JSON: {exc}") from exc
            payload[key] = value
        return payload

    @property
    def editing_routine_id(self):
        return self._routine_id or None


class RoutinesView(QWidget):
    refresh_requested = Signal()
    create_requested = Signal()
    select_routine_requested = Signal(str)
    edit_routine_requested = Signal(str)
    delete_routine_requested = Signal(str)
    run_routine_requested = Signal(str)
    routine_create_submitted = Signal(dict)
    routine_edit_submitted = Signal(str, dict)
    routine_run_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.routines_index = {}
        self.tasks_index = {}
        self.projects_index = {}
        self.editor = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel("<h2>Routines</h2>"))

        body = QHBoxLayout()
        self.list = RoutinesListView()
        self.list.refresh_requested.connect(self.refresh_requested.emit)
        self.list.create_routine_requested.connect(self.create_requested.emit)
        self.list.select_routine_requested.connect(self.select_routine_requested.emit)
        self.list.setMaximumWidth(320)
        body.addWidget(self.list)

        self.detail = RoutineDetailView()
        self.detail.refresh_requested.connect(lambda rid: self.select_routine_requested.emit(rid))
        self.detail.edit_requested.connect(self.edit_routine_requested.emit)
        self.detail.delete_requested.connect(self.delete_routine_requested.emit)
        self.detail.run_requested.connect(self.run_routine_requested.emit)
        body.addWidget(self.detail, 1)

        root.addLayout(body, 1)
        self.set_routines([])

    def set_routines(self, routines):
        self.routines_index = {str(r.get("routine_id")): r for r in routines if r.get("routine_id")}
        self.list.set_routines(routines)

    def set_routine(self, routine, runs=None):
        self.routines_index[str(routine.get("routine_id") or "")] = routine
        self.detail.set_routine(routine, runs)

    def set_runs(self, routine_id, runs):
        if self.detail.routine_id == routine_id:
            self.detail.set_runs(runs)

    def set_tasks(self, tasks):
        self.tasks_index = {str(t.get("task_id")): t for t in (tasks or []) if t.get("task_id")}

    def set_projects(self, projects):
        self.projects_index = {str(p.get("project_id")): p for p in (projects or []) if p.get("project_id")}

    def show_create_editor(self):
        self.editor = RoutineEditorDialog(
            available_tasks=list(self.tasks_index.values()),
            available_projects=list(self.projects_index.values()),
            parent=self,
        )
        self.editor.accepted_payload.connect(self.routine_create_submitted.emit)
        self.editor.open()

    def show_edit_editor(self, routine_id):
        routine = self.routines_index.get(routine_id)
        if not routine:
            return
        self.editor = RoutineEditorDialog(
            routine=routine,
            available_tasks=list(self.tasks_index.values()),
            available_projects=list(self.projects_index.values()),
            parent=self,
        )
        self.editor.accepted_payload.connect(
            lambda payload, rid=routine_id: self.routine_edit_submitted.emit(rid, payload)
        )
        self.editor.open()
