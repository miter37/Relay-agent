from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..util import is_within, json_dump, json_load, safe_resolve, utc_now

_ACTIVE_RUN_STATUSES = {
    "PLANNED",
    "QUEUED",
    "PREPARING",
    "RUNNING",
    "VALIDATING",
    "DELIVERING",
    "CANCEL_REQUESTED",
}
_SUCCESS_STATUSES = {"COMPLETED"}
_MARKER_NAME = ".relay-schedule-run.json"


class ScheduleRetentionManager:
    """Remove only Relay-owned Schedule run directories according to each policy."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        runtime = config.path_value("runtime_root")
        self.state_path = runtime / "schedule-retention-state.json"
        self.lock_path = runtime / "schedule-retention.lock"

    def _acquire_lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                if time.time() - self.lock_path.stat().st_mtime > 7200:
                    self.lock_path.unlink(missing_ok=True)
                    return self._acquire_lock()
            except OSError:
                pass
            return False
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{os.getpid()}\n")
        return True

    def _release_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _has_symlink_below(path: Path, root: Path) -> bool:
        current = Path(os.path.abspath(path.expanduser()))
        boundary = safe_resolve(root)
        while True:
            if current.is_symlink():
                return True
            if safe_resolve(current) == boundary:
                return False
            if current == current.parent:
                return True
            current = current.parent

    @staticmethod
    def _retention(schedule: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(schedule.get("retention_json") or "{}")
        except json.JSONDecodeError:
            value = {}
        mode = value.get("mode", "days") if isinstance(value, dict) else "days"
        amount = value.get("value", 90) if isinstance(value, dict) else 90
        if mode not in {"days", "latest_runs", "forever"}:
            mode = "days"
        return {"mode": mode, "value": max(1, int(amount))} if mode != "forever" else {"mode": mode}

    def _refresh_run_status(self, run: dict[str, Any]) -> dict[str, Any]:
        job_id = run.get("job_id")
        if not job_id:
            return run
        job = self.db.get_job(str(job_id))
        if not job or not job.get("status") or job["status"] == run.get("status"):
            return run
        updated = dict(run)
        updated["status"] = job["status"]
        self.db.update_schedule_run(str(run["run_id"]), status=job["status"])
        return updated

    def _run_is_active(self, run: dict[str, Any]) -> bool:
        if run.get("status") in _ACTIVE_RUN_STATUSES:
            return True
        job_id = run.get("job_id")
        if not job_id:
            return False
        job = self.db.get_job(str(job_id))
        return bool(job and job.get("status") in _ACTIVE_RUN_STATUSES)

    def _owned_run_root(
        self, schedule: dict[str, Any], run: dict[str, Any]
    ) -> tuple[Path | None, dict[str, Any] | None]:
        output_value = run.get("output_path")
        root_value = schedule.get("output_root")
        if not output_value or not root_value:
            return None, {
                "code": "SCHEDULE_PATH_NOT_ALLOWED",
                "error": "Schedule run has no output root.",
                "retryable": False,
            }
        raw_root = Path(str(root_value)).expanduser()
        raw_output = Path(str(output_value)).expanduser()
        if raw_root.is_symlink() or self._has_symlink_below(raw_output, raw_root):
            return None, {
                "code": "SCHEDULE_PATH_NOT_ALLOWED",
                "error": "Schedule output contains a symlink.",
                "retryable": False,
            }
        root = safe_resolve(raw_root)
        run_root = Path(os.path.abspath(raw_output.parent))
        resolved_run_root = safe_resolve(run_root)
        if resolved_run_root == root or not is_within(resolved_run_root, root):
            return None, {
                "code": "SCHEDULE_PATH_NOT_ALLOWED",
                "error": "Schedule run escapes its output root.",
                "retryable": False,
            }
        marker_path = run_root / _MARKER_NAME
        if not marker_path.is_file():
            return None, {"code": "SCHEDULE_OUTPUT_UNOWNED", "error": "Run marker is missing.", "retryable": False}
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None, {"code": "SCHEDULE_OUTPUT_UNOWNED", "error": "Run marker is invalid.", "retryable": False}
        if marker.get("schedule_id") != schedule.get("schedule_id") or marker.get("run_id") != run.get("run_id"):
            return None, {
                "code": "SCHEDULE_OUTPUT_UNOWNED",
                "error": "Run marker identity does not match history.",
                "retryable": False,
            }
        if not run_root.is_dir() or run_root.is_symlink():
            return None, {
                "code": "SCHEDULE_PATH_NOT_ALLOWED",
                "error": "Schedule run root is not a directory.",
                "retryable": False,
            }
        return run_root, None

    def _run_locked(self, *, dry_run: bool, now: datetime) -> dict[str, Any]:
        report: dict[str, Any] = {
            "ok": True,
            "started_at": utc_now(),
            "completed_at": None,
            "dry_run": dry_run,
            "removed": [],
            "skipped_active": 0,
            "skipped_retained": 0,
            "missing": 0,
            "errors": [],
        }
        for schedule in self.db.list_schedules(include_deleted=True):
            runs = [
                self._refresh_run_status(run)
                for run in self.db.list_schedule_runs(schedule["schedule_id"], limit=100000)
            ]
            runs.sort(
                key=lambda run: self._parse_time(run.get("scheduled_for_utc")) or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            policy = self._retention(schedule)
            newest_success = next((run.get("run_id") for run in runs if run.get("status") in _SUCCESS_STATUSES), None)
            keep_count = policy.get("value") if policy["mode"] == "latest_runs" else None
            for index, run in enumerate(runs):
                run_root, error = self._owned_run_root(schedule, run)
                if error:
                    if error["code"] == "SCHEDULE_OUTPUT_UNOWNED":
                        report["skipped_retained"] += 1
                    else:
                        report["errors"].append({**error, "run_id": str(run.get("run_id"))})
                    continue
                if self._run_is_active(run):
                    report["skipped_active"] += 1
                    continue
                if run.get("run_id") == newest_success or (keep_count is not None and index < keep_count):
                    report["skipped_retained"] += 1
                    continue
                if policy["mode"] == "forever":
                    report["skipped_retained"] += 1
                    continue
                if policy["mode"] == "days":
                    scheduled = self._parse_time(run.get("scheduled_for_utc"))
                    if scheduled is None or scheduled > now - timedelta(days=policy["value"]):
                        report["skipped_retained"] += 1
                        continue
                if run_root is None or not run_root.exists():
                    report["missing"] += 1
                    continue
                if dry_run:
                    report["removed"].append(str(run_root))
                    continue
                try:
                    shutil.rmtree(run_root)
                    report["removed"].append(str(run_root))
                except OSError as exc:
                    report["errors"].append(
                        {"run_id": str(run.get("run_id")), "path": str(run_root), "error": str(exc), "retryable": True}
                    )
        report["completed_at"] = utc_now()
        report["ok"] = not report["errors"]
        if not dry_run:
            previous = json_load(self.state_path, {}) or {}
            json_dump(
                self.state_path,
                {
                    "last_attempt": report["completed_at"],
                    "last_run": report["completed_at"] if report["ok"] else previous.get("last_run"),
                    "last_report": report,
                },
            )
        return report

    def run(self, *, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
        locked = dry_run or self._acquire_lock()
        if not locked:
            return {"ok": True, "status": "skipped", "reason": "schedule_retention_already_running", "dry_run": dry_run}
        try:
            return self._run_locked(dry_run=dry_run, now=(now or datetime.now(UTC)).astimezone(UTC))
        finally:
            if not dry_run:
                self._release_lock()

    def due(self) -> bool:
        if not bool(self.config.get("schedule_retention_enabled", True)):
            return False
        interval_hours = max(1, int(self.config.get("schedule_retention_interval_hours", 24)))
        state = json_load(self.state_path, {}) or {}
        last = self._parse_time(state.get("last_run"))
        if last is None:
            return bool(self.config.get("schedule_retention_run_on_daemon_start", True))
        return datetime.now(UTC) >= last + timedelta(hours=interval_hours)

    def status(self) -> dict[str, Any]:
        state = json_load(self.state_path, {}) or {}
        return {
            "enabled": bool(self.config.get("schedule_retention_enabled", True)),
            "interval_hours": int(self.config.get("schedule_retention_interval_hours", 24)),
            "last_attempt": state.get("last_attempt"),
            "last_run": state.get("last_run"),
            "last_report": state.get("last_report"),
        }
