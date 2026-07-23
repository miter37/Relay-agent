from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class ScheduleEditorDialog(QDialog):
    preview_requested = Signal(dict)
    save_requested = Signal(dict)

    _RULE_LABELS = {
        "daily": "Daily",
        "weekly": "Weekly",
        "monthly": "Monthly",
        "n_days": "Every N days",
        "once": "One time",
    }

    def __init__(self, *, source_job_id: str, parent=None):
        super().__init__(parent)
        self.source_job_id = source_job_id
        self.setWindowTitle("Schedule this task")
        self.resize(560, 620)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Schedule name")
        form.addRow("Schedule name", self.name_edit)

        self.type_combo = QComboBox()
        for rule_type, label in self._RULE_LABELS.items():
            self.type_combo.addItem(label, rule_type)
        self.type_combo.currentIndexChanged.connect(self._rule_type_changed)
        form.addRow("Repeat", self.type_combo)

        self.times_edit = QLineEdit("09:00")
        self.times_edit.setPlaceholderText("09:00, 13:00")
        form.addRow("Times", self.times_edit)

        weekday_row = QHBoxLayout()
        self.weekday_checks: list[QCheckBox] = []
        for day, label in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), start=1):
            checkbox = QCheckBox(label)
            checkbox.setProperty("iso_weekday", day)
            self.weekday_checks.append(checkbox)
            weekday_row.addWidget(checkbox)
        form.addRow("Days", weekday_row)

        self.month_days_edit = QLineEdit()
        self.month_days_edit.setPlaceholderText("1, 15, 28")
        form.addRow("Month dates", self.month_days_edit)

        self.interval_days = QSpinBox()
        self.interval_days.setRange(1, 3650)
        self.interval_days.setValue(1)
        form.addRow("Interval days", self.interval_days)

        self.anchor_date_edit = QLineEdit()
        self.anchor_date_edit.setPlaceholderText("2026-07-23")
        form.addRow("Start date", self.anchor_date_edit)

        self.run_at_local_edit = QLineEdit()
        self.run_at_local_edit.setPlaceholderText("2026-08-03T10:30")
        form.addRow("Run at", self.run_at_local_edit)

        self.timezone_edit = QLineEdit("UTC")
        self.timezone_edit.setPlaceholderText("IANA timezone, e.g. Asia/Seoul")
        form.addRow("Time zone", self.timezone_edit)

        self.overlap_policy = QComboBox()
        self.overlap_policy.addItem("Skip if previous run is active", "skip")
        self.overlap_policy.addItem("Add to waiting", "queue")
        form.addRow("Active run", self.overlap_policy)

        self.missed_policy = QComboBox()
        self.missed_policy.addItem("Skip missed runs", "skip")
        self.missed_policy.addItem("Run one catch-up", "catch_up")
        form.addRow("Missed runs", self.missed_policy)

        self.missed_grace_seconds = QSpinBox()
        self.missed_grace_seconds.setRange(0, 7 * 24 * 60 * 60)
        self.missed_grace_seconds.setValue(12 * 60 * 60)
        form.addRow("Catch-up grace (seconds)", self.missed_grace_seconds)

        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("2026-07-24T00:00:00+00:00")
        form.addRow("Start bound", self.start_edit)

        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("Optional ISO datetime with timezone")
        form.addRow("End bound", self.end_edit)

        self.output_root_edit = QLineEdit()
        self.output_root_edit.setPlaceholderText("Optional output base folder")
        form.addRow("Output folder", self.output_root_edit)

        self.retention_mode = QComboBox()
        self.retention_mode.addItems(["days", "latest_runs", "forever"])
        self.retention_mode.currentTextChanged.connect(self._retention_changed)
        form.addRow("Keep outputs", self.retention_mode)

        self.retention_value = QSpinBox()
        self.retention_value.setRange(1, 100000)
        self.retention_value.setValue(90)
        form.addRow("Retention value", self.retention_value)

        root.addLayout(form)
        root.addWidget(QLabel("Next five runs"))
        self.preview_list = QListWidget()
        root.addWidget(self.preview_list, 1)
        self.preview_error = QLabel()
        self.preview_error.setWordWrap(True)
        root.addWidget(self.preview_error)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self._request_preview)
        actions.addWidget(self.preview_button)
        actions.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton("Create schedule")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.payload()))
        actions.addWidget(self.save_button)
        root.addLayout(actions)

        self._rule_type_changed()
        self._retention_changed(self.retention_mode.currentText())

    def _rule_type_changed(self) -> None:
        rule_type = self.type_combo.currentData()
        for widget in (
            self.weekday_checks,
            self.month_days_edit,
            self.interval_days,
            self.anchor_date_edit,
            self.run_at_local_edit,
        ):
            if isinstance(widget, list):
                for item in widget:
                    item.setVisible(rule_type == "weekly")
            else:
                widget.setVisible(
                    (rule_type == "monthly" and widget is self.month_days_edit)
                    or (rule_type == "n_days" and widget in {self.interval_days, self.anchor_date_edit})
                    or (rule_type == "once" and widget is self.run_at_local_edit)
                )

    def _retention_changed(self, mode: str) -> None:
        self.retention_value.setEnabled(mode != "forever")

    @staticmethod
    def _split_values(value: str) -> list[str]:
        return sorted({item.strip() for item in value.split(",") if item.strip()})

    def payload(self) -> dict:
        rule_type = str(self.type_combo.currentData())
        rule = {"type": rule_type, "timezone": self.timezone_edit.text().strip()}
        if rule_type != "once":
            rule["times"] = self._split_values(self.times_edit.text())
        if rule_type == "weekly":
            rule["weekdays"] = [int(item.property("iso_weekday")) for item in self.weekday_checks if item.isChecked()]
        elif rule_type == "monthly":
            rule["month_days"] = [int(item) for item in self._split_values(self.month_days_edit.text())]
            rule["missing_day_policy"] = "skip"
        elif rule_type == "n_days":
            rule["interval_days"] = self.interval_days.value()
            rule["anchor_date"] = self.anchor_date_edit.text().strip()
        elif rule_type == "once":
            rule["run_at_local"] = self.run_at_local_edit.text().strip()

        payload = {
            "name": self.name_edit.text().strip(),
            "rule": rule,
            "overlap_policy": self.overlap_policy.currentData(),
            "missed_policy": self.missed_policy.currentData(),
            "missed_grace_seconds": self.missed_grace_seconds.value(),
            "retention": {"mode": self.retention_mode.currentText()},
        }
        if self.retention_mode.currentText() != "forever":
            payload["retention"]["value"] = self.retention_value.value()
        for field, target in (("start", "starts_at_utc"), ("end", "ends_at_utc"), ("output_root", "output_root")):
            value = getattr(self, f"{field}_edit").text().strip()
            if value:
                payload[target] = value
        return payload

    def _request_preview(self) -> None:
        self.preview_error.clear()
        self.save_button.setEnabled(False)
        self.preview_requested.emit(self.payload())

    def set_preview(self, occurrences: list[dict]) -> None:
        self.preview_list.clear()
        for item in occurrences:
            self.preview_list.addItem(str(item.get("local") or item.get("utc") or "—"))
        self.preview_error.clear()
        self.save_button.setEnabled(bool(occurrences))

    def set_preview_error(self, message: str) -> None:
        self.preview_list.clear()
        self.preview_error.setText(message)
        self.save_button.setEnabled(False)

    def set_schedule(self, schedule: dict) -> None:
        rule = schedule.get("rule") or {}
        self.name_edit.setText(str(schedule.get("name") or ""))
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(rule.get("type"))))
        self.times_edit.setText(", ".join(rule.get("times") or []))
        self.timezone_edit.setText(str(rule.get("timezone") or "UTC"))
        for checkbox in self.weekday_checks:
            checkbox.setChecked(int(checkbox.property("iso_weekday")) in (rule.get("weekdays") or []))
        self.month_days_edit.setText(", ".join(str(item) for item in rule.get("month_days") or []))
        self.interval_days.setValue(int(rule.get("interval_days") or 1))
        self.anchor_date_edit.setText(str(rule.get("anchor_date") or ""))
        self.run_at_local_edit.setText(str(rule.get("run_at_local") or ""))
        self.overlap_policy.setCurrentIndex(
            max(0, self.overlap_policy.findData(schedule.get("overlap_policy", "skip")))
        )
        self.missed_policy.setCurrentIndex(max(0, self.missed_policy.findData(schedule.get("missed_policy", "skip"))))
        self.missed_grace_seconds.setValue(int(schedule.get("missed_grace_seconds") or 43200))
        self.start_edit.setText(str(schedule.get("starts_at_utc") or ""))
        self.end_edit.setText(str(schedule.get("ends_at_utc") or ""))
        output_root = schedule.get("output_root")
        self.output_root_edit.setText(str(Path(output_root).parent) if output_root else "")
        retention = schedule.get("retention") or {}
        self.retention_mode.setCurrentText(str(retention.get("mode") or "days"))
        self.retention_value.setValue(int(retention.get("value") or 90))
