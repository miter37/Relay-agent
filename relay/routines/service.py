from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..config import Config
from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..schedules.rules import Occurrence, next_occurrences, validate_rule
from ..util import new_job_id
from .models import RoutineSpec


class RoutineService:
    def __init__(self, config: Config, db: Database, engine: RelayEngine):
        self.config = config
        self.db = db
        self.engine = engine

    # ---- CRUD ----

    def create_routine(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = RoutineSpec.from_dict(payload)
        spec.validate(
            task_lookup=lambda tid: self.db.get_task(tid),
            project_lookup=lambda pid: self.db.get_project(pid),
        )
        row = spec.to_row()
        routine_id = new_job_id()
        next_run = self._compute_next_run(spec)
        record = {
            "routine_id": routine_id,
            "name": spec.name,
            "target_type": spec.target_type,
            "target_id": spec.target_id,
            "rule_json": row["rule_json"],
            "timezone": spec.timezone,
            "enabled": 1 if spec.enabled else 0,
            "overlap_policy": spec.overlap_policy,
            "missed_policy": spec.missed_policy,
            "missed_grace_seconds": spec.missed_grace_seconds,
            "version_policy": spec.version_policy,
            "pinned_version": spec.pinned_version,
            "input_policy_json": row["input_policy_json"],
            "notification_policy_json": row["notification_policy_json"],
            "starts_at_utc": spec.starts_at_utc,
            "ends_at_utc": spec.ends_at_utc,
            "next_run_at_utc": next_run,
            "last_occurrence_key": None,
        }
        self.db.create_routine(record)
        return self.db.get_routine(routine_id)

    def update_routine(self, routine_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.db.get_routine(routine_id)
        if not existing or existing.get("deleted_at") is not None:
            raise RelayError("ROUTINE_NOT_FOUND", f"Routine not found: {routine_id}")
        merged = {
            "name": payload.get("name", existing["name"]),
            "target_type": payload.get("target_type", existing["target_type"]),
            "target_id": payload.get("target_id", existing["target_id"]),
            "rule": payload.get("rule"),
            "timezone": payload.get("timezone", existing["timezone"]),
            "overlap_policy": payload.get("overlap_policy", existing["overlap_policy"]),
            "missed_policy": payload.get("missed_policy", existing["missed_policy"]),
            "missed_grace_seconds": payload.get("missed_grace_seconds", existing["missed_grace_seconds"]),
            "version_policy": payload.get("version_policy", existing["version_policy"]),
            "pinned_version": payload.get("pinned_version", existing.get("pinned_version")),
            "input_policy": payload.get("input_policy"),
            "notification_policy": payload.get("notification_policy"),
            "starts_at_utc": payload.get("starts_at_utc", existing.get("starts_at_utc")),
            "ends_at_utc": payload.get("ends_at_utc", existing.get("ends_at_utc")),
            "enabled": bool(payload.get("enabled", existing["enabled"])),
        }
        spec = RoutineSpec.from_dict(merged)
        spec.validate(
            task_lookup=lambda tid: self.db.get_task(tid),
            project_lookup=lambda pid: self.db.get_project(pid),
        )
        next_run = self._compute_next_run(spec)
        changes = {
            "name": spec.name,
            "target_type": spec.target_type,
            "target_id": spec.target_id,
            "rule_json": spec.to_row()["rule_json"],
            "timezone": spec.timezone,
            "overlap_policy": spec.overlap_policy,
            "missed_policy": spec.missed_policy,
            "missed_grace_seconds": spec.missed_grace_seconds,
            "version_policy": spec.version_policy,
            "pinned_version": spec.pinned_version,
            "input_policy_json": spec.to_row()["input_policy_json"],
            "notification_policy_json": spec.to_row()["notification_policy_json"],
            "starts_at_utc": spec.starts_at_utc,
            "ends_at_utc": spec.ends_at_utc,
            "enabled": 1 if spec.enabled else 0,
            "next_run_at_utc": next_run,
        }
        self.db.update_routine(routine_id, **changes)
        return self.db.get_routine(routine_id)

    def soft_delete_routine(self, routine_id: str) -> bool:
        return self.db.soft_delete_routine(routine_id)

    def get_routine(self, routine_id: str) -> dict[str, Any]:
        routine = self.db.get_routine(routine_id)
        if not routine or routine.get("deleted_at") is not None:
            raise RelayError("ROUTINE_NOT_FOUND", f"Routine not found: {routine_id}")
        return routine

    def list_routines(self, *, name: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.list_routines(name=name, limit=limit)

    # ---- Preview / Run-now ----

    def preview(self, payload: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
        rule = payload.get("rule") or {}
        if isinstance(rule, str):
            rule = json.loads(rule)
        rule_with_tz = dict(rule)
        rule_with_tz.setdefault("timezone", payload.get("timezone") or "UTC")
        validate_rule(rule_with_tz)
        starts = payload.get("starts_at_utc")
        ends = payload.get("ends_at_utc")
        anchor = datetime.now(UTC)
        items = next_occurrences(
            rule,
            anchor - _utc_to_dt(starts) + _utc_to_dt(starts),
            limit=limit,
            starts_at_utc=_utc_to_dt(starts),
            ends_at_utc=_utc_to_dt(ends),
        )
        return {"items": [_occ_public(o) for o in items]}

    def run_now(self, routine_id: str) -> dict[str, Any]:
        from ..routines.runtime import RoutineRuntime

        routine = self.get_routine(routine_id)
        now = datetime.now(UTC)
        occurrence = self._make_manual_occurrence(routine, now)
        run_row = self._build_run_row(routine, occurrence, trigger_type="routine", status="pending")
        if not self.db.claim_routine_occurrence(routine_id, run_row):
            raise RelayError("INVALID_REQUEST", "A run for this occurrence is already in progress.")

        rt = RoutineRuntime(self.engine.config, self.db, self.engine, self)
        rt._dispatch(routine, run_row["run_id"])
        return self.db.get_routine_run(run_row["run_id"])

    # ---- Reconciliation ----

    def reconcile_run(self, run_id: str) -> dict[str, Any]:
        run = self.db.get_routine_run(run_id)
        if not run:
            raise RelayError("INVALID_REQUEST", f"Routine run not found: {run_id}")
        if run["status"] not in {"pending", "running"}:
            return run
        status = "completed"
        error_code = None
        error_message = None
        if run["task_run_id"]:
            job = self.db.get_job(run["task_run_id"])
            if job:
                if job["status"] == "COMPLETED":
                    status = "completed"
                elif job["status"] in {"FAILED", "CANCELLED"}:
                    status = "failed"
                    error_code = job.get("error_code")
                    error_message = job.get("error_message")
                else:
                    status = "running"
        elif run["project_run_id"]:
            project_run = self.db.get_project_run(run["project_run_id"])
            if project_run:
                if project_run["status"] == "completed":
                    status = "completed"
                elif project_run["status"] == "failed":
                    status = "failed"
                    error_code = project_run.get("error_code")
                    error_message = project_run.get("error_message")
                else:
                    status = "running"
        changes: dict[str, Any] = {"status": status}
        if error_code is not None:
            changes["error_code"] = error_code
        if error_message is not None:
            changes["error_message"] = error_message
        self.db.update_routine_run(run_id, **changes)
        return self.db.get_routine_run(run_id)

    # ---- Receipt ----

    def routine_receipt(self, routine_id: str) -> dict[str, Any]:
        routine = self.get_routine(routine_id)
        runs = self.db.list_routine_runs(routine_id=routine_id, limit=100)
        return {"routine": routine, "runs": runs}

    # ---- Helpers ----

    def _compute_next_run(self, spec: RoutineSpec) -> str | None:
        rule = dict(spec.rule)
        rule.setdefault("timezone", spec.timezone)
        starts = _utc_to_dt(spec.starts_at_utc)
        ends = _utc_to_dt(spec.ends_at_utc)
        anchor = datetime.now(UTC)
        if starts and anchor < starts:
            anchor = starts
        items = next_occurrences(rule, anchor, limit=1, starts_at_utc=starts, ends_at_utc=ends)
        return items[0].instant_utc.isoformat(timespec="seconds") if items else None

    def _make_manual_occurrence(self, routine: dict[str, Any], when_utc: datetime) -> Occurrence:
        local = when_utc.astimezone(_zone(routine["timezone"]))
        return Occurrence(
            instant_utc=when_utc,
            local_time=local.replace(microsecond=0),
            occurrence_key=when_utc.strftime("%Y-%m-%dT%H:%M"),
        )

    def _build_run_row(
        self, routine: dict[str, Any], occurrence: Occurrence, *, trigger_type: str, status: str
    ) -> dict[str, Any]:
        return {
            "run_id": new_job_id(),
            "occurrence_key": occurrence.occurrence_key,
            "scheduled_for_utc": occurrence.instant_utc.isoformat(timespec="seconds"),
            "scheduled_for_local": occurrence.local_time.isoformat(timespec="minutes"),
            "trigger_type": trigger_type,
            "status": status,
            "target_type": routine["target_type"],
        }


def _utc_to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise RelayError("INVALID_REQUEST", f"Invalid ISO datetime: {value}") from exc
    if parsed.tzinfo is None:
        raise RelayError("INVALID_REQUEST", "Datetime must include timezone.")
    return parsed.astimezone(UTC)


def _zone(value: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(value)


def _occ_public(occ: Occurrence) -> dict[str, Any]:
    return {
        "instant_utc": occ.instant_utc.isoformat(timespec="seconds"),
        "local_time": occ.local_time.isoformat(timespec="minutes"),
        "occurrence_key": occ.occurrence_key,
    }
