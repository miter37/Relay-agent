from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database
from .util import json_dump, json_load, safe_resolve, utc_now

_ACTIVE_STATUSES = {
    "CREATED", "QUEUED", "PREPARING", "RUNNING", "VALIDATING", "DELIVERING", "CANCEL_REQUESTED"
}
_FINAL_STATUS_KEYS = {
    "COMPLETED": "retention_days_completed",
    "PARTIAL": "retention_days_partial",
    "FAILED": "retention_days_failed",
    "CANCELLED": "retention_days_cancelled",
}


@dataclass(slots=True)
class CleanupReport:
    started_at: str
    dry_run: bool
    override_days: int | None = None
    removed: list[str] = field(default_factory=list)
    skipped_active: int = 0
    skipped_retained: int = 0
    missing: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.errors,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "dry_run": self.dry_run,
            "override_days": self.override_days,
            "removed_count": len(self.removed),
            "removed": self.removed,
            "skipped_active": self.skipped_active,
            "skipped_retained": self.skipped_retained,
            "missing": self.missing,
            "errors": self.errors,
        }


class CleanupManager:
    """Deletes expired job workspaces while preserving final results and DB history."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.state_path = config.path_value("runtime_root") / "cleanup-state.json"
        self.lock_path = config.path_value("runtime_root") / "cleanup.lock"

    def _acquire_lock(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - self.lock_path.stat().st_mtime
                if age > 7200:
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

    def policy_days(self, status: str, override_days: int | None = None) -> int:
        if override_days is not None:
            return max(0, int(override_days))
        key = _FINAL_STATUS_KEYS.get(status)
        if key:
            return max(0, int(self.config.get(key, self.config.get("retention_days", 30))))
        return max(0, int(self.config.get("retention_days", 30)))

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _job_expired(self, job: dict[str, Any], now: datetime, override_days: int | None) -> bool:
        status = str(job.get("status") or "")
        if status in _ACTIVE_STATUSES:
            return False
        reference = self._parse_time(job.get("completed_at")) or self._parse_time(job.get("updated_at"))
        reference = reference or self._parse_time(job.get("created_at"))
        if reference is None:
            return False
        return reference <= now - timedelta(days=self.policy_days(status, override_days))

    def _candidate_paths(self, job_id: str) -> list[Path]:
        candidates: list[Path] = []
        for root_key in ("workspace_root", "staging_root"):
            root = safe_resolve(self.config.path_value(root_key))
            for worker in ("claude", "codex", "antigravity"):
                target = safe_resolve(root / worker / job_id)
                try:
                    target.relative_to(root)
                except ValueError:
                    continue
                candidates.append(target)
        return candidates

    @staticmethod
    def _remove_empty_parents(path: Path, stop: Path) -> None:
        current = path.parent
        stop = safe_resolve(stop)
        while current != stop:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _cleanup_orphans(self, report: CleanupReport, now: datetime) -> None:
        days = int(self.config.get("cleanup_orphan_days", 7))
        cutoff = now - timedelta(days=max(0, days))
        known = {str(job["job_id"]) for job in self.db.list_jobs(limit=100000)}
        for root_key in ("workspace_root", "staging_root"):
            root = safe_resolve(self.config.path_value(root_key))
            if not root.exists():
                continue
            for worker in ("claude", "codex", "antigravity"):
                worker_root = root / worker
                if not worker_root.is_dir():
                    continue
                for child in worker_root.iterdir():
                    if not child.is_dir() or child.name in known:
                        continue
                    try:
                        modified = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
                    except OSError:
                        continue
                    if modified > cutoff:
                        continue
                    if report.dry_run:
                        report.removed.append(str(child))
                        continue
                    try:
                        shutil.rmtree(child)
                        report.removed.append(str(child))
                    except OSError as exc:
                        report.errors.append({"path": str(child), "error": str(exc)})

    def run(self, *, override_days: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        locked = dry_run or self._acquire_lock()
        if not locked:
            return {"ok": True, "status": "skipped", "reason": "cleanup_already_running", "dry_run": dry_run}
        try:
            return self._run_locked(override_days=override_days, dry_run=dry_run)
        finally:
            if not dry_run:
                self._release_lock()

    def _run_locked(self, *, override_days: int | None = None, dry_run: bool = False) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        report = CleanupReport(started_at=utc_now(), dry_run=dry_run, override_days=override_days)
        remove_empty = bool(self.config.get("cleanup_remove_empty_parents", True))

        for job in self.db.list_jobs(limit=100000):
            status = str(job.get("status") or "")
            if status in _ACTIVE_STATUSES:
                report.skipped_active += 1
                continue
            if not self._job_expired(job, now, override_days):
                report.skipped_retained += 1
                continue
            for target in self._candidate_paths(str(job["job_id"])):
                if not target.exists():
                    report.missing += 1
                    continue
                if report.dry_run:
                    report.removed.append(str(target))
                    continue
                try:
                    root = target.parents[1]
                    shutil.rmtree(target)
                    report.removed.append(str(target))
                    if remove_empty:
                        self._remove_empty_parents(target, root)
                except OSError as exc:
                    report.errors.append({"path": str(target), "error": str(exc)})

        if bool(self.config.get("cleanup_remove_orphans", True)):
            self._cleanup_orphans(report, now)

        report.completed_at = utc_now()
        result = report.to_dict()
        if not dry_run:
            previous = json_load(self.state_path, {}) or {}
            state = {
                "last_attempt": report.completed_at,
                "last_run": report.completed_at if not report.errors else previous.get("last_run"),
                "last_report": result,
            }
            json_dump(self.state_path, state)
        return result

    def due(self) -> bool:
        if not bool(self.config.get("cleanup_enabled", True)):
            return False
        interval_hours = max(1, int(self.config.get("cleanup_interval_hours", 24)))
        state = json_load(self.state_path, {}) or {}
        last = self._parse_time(state.get("last_run"))
        if last is None:
            return bool(self.config.get("cleanup_run_on_daemon_start", True))
        return datetime.now(timezone.utc) >= last + timedelta(hours=interval_hours)

    def status(self) -> dict[str, Any]:
        state = json_load(self.state_path, {}) or {}
        return {
            "enabled": bool(self.config.get("cleanup_enabled", True)),
            "interval_hours": int(self.config.get("cleanup_interval_hours", 24)),
            "last_attempt": state.get("last_attempt"),
            "last_run": state.get("last_run"),
            "last_report": state.get("last_report"),
            "retention": {
                "completed_days": int(self.config.get("retention_days_completed", 7)),
                "partial_days": int(self.config.get("retention_days_partial", 14)),
                "failed_days": int(self.config.get("retention_days_failed", 30)),
                "cancelled_days": int(self.config.get("retention_days_cancelled", 14)),
                "orphan_days": int(self.config.get("cleanup_orphan_days", 7)),
            },
        }
