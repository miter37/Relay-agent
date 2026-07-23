from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Config
from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..util import new_job_id, safe_resolve, utc_now
from .rules import next_occurrences, validate_rule
from .snapshots import materialize_snapshot, validate_source_job


class ScheduleService:
    def __init__(self, config: Config, db: Database, engine: RelayEngine):
        self.config = config
        self.db = db
        self.engine = engine

    @staticmethod
    def _parse_retention(value: Any) -> dict[str, Any]:
        if value is None:
            return {"mode": "days", "value": 90}
        if not isinstance(value, dict):
            raise RelayError("INVALID_REQUEST", "retention must be an object")
        mode = value.get("mode", "days")
        if mode not in {"days", "latest_runs", "forever"}:
            raise RelayError("INVALID_REQUEST", "retention mode must be days, latest_runs, or forever")
        if mode == "forever":
            return {"mode": mode}
        amount = value.get("value")
        if not isinstance(amount, int) or amount < 1:
            raise RelayError("INVALID_REQUEST", "retention value must be a positive integer")
        return {"mode": mode, "value": amount}

    @staticmethod
    def _parse_datetime(value: Any, field: str) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            raise RelayError("INVALID_REQUEST", f"{field} must be an ISO datetime") from None
        if parsed.tzinfo is None:
            raise RelayError("INVALID_REQUEST", f"{field} must include a timezone")
        return parsed.astimezone(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _decode(value: str | None, fallback: Any) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback

    def _require(self, schedule_id: str) -> dict[str, Any]:
        schedule = self.db.get_schedule(schedule_id)
        if not schedule or schedule.get("deleted_at"):
            raise RelayError("SCHEDULE_NOT_FOUND", f"Schedule not found: {schedule_id}")
        return schedule

    def _public(self, schedule: dict[str, Any]) -> dict[str, Any]:
        result = dict(schedule)
        result["rule"] = self._decode(schedule.get("rule_json"), {})
        result["retention"] = self._decode(schedule.get("retention_json"), {})
        result.pop("rule_json", None)
        result.pop("retention_json", None)
        return result

    def create_from_job(self, source_job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.get("service_isolation_acknowledged", False):
            raise RelayError(
                "PERMISSION_BLOCKED",
                "Schedule execution requires service isolation acknowledgement before creation.",
            )
        source = self.db.get_job(source_job_id)
        if not source:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {source_job_id}")
        rule = validate_rule(payload.get("rule"))
        request = validate_source_job(source, self.engine.agent_registry)
        name = str(payload.get("name") or source.get("title") or f"Schedule {source_job_id[:8]}").strip()
        if not name:
            raise RelayError("INVALID_REQUEST", "Schedule name is required")
        schedule_id = new_job_id()
        snapshot = materialize_snapshot(self.config, schedule_id, request.task, request.attachments)
        output_root = safe_resolve(
            Path(str(payload.get("output_root") or self.config.home / "schedule-outputs")) / schedule_id
        )
        if output_root.exists() and output_root.is_symlink():
            raise RelayError("SCHEDULE_PATH_NOT_ALLOWED", "Schedule output root cannot be a symlink")
        output_root.mkdir(parents=True, exist_ok=True)
        retention = self._parse_retention(payload.get("retention"))
        overlap_policy = payload.get("overlap_policy", "skip")
        missed_policy = payload.get("missed_policy", "skip")
        if overlap_policy not in {"skip", "queue"}:
            raise RelayError("INVALID_REQUEST", "overlap_policy must be skip or queue")
        if missed_policy not in {"skip", "catch_up"}:
            raise RelayError("INVALID_REQUEST", "missed_policy must be skip or catch_up")
        grace = payload.get("missed_grace_seconds", 43200)
        if not isinstance(grace, int) or grace < 0:
            raise RelayError("INVALID_REQUEST", "missed_grace_seconds must be non-negative")
        now = datetime.now(UTC)
        occurrences = next_occurrences(rule, now, limit=1)
        if not occurrences:
            raise RelayError("SCHEDULE_RULE_INVALID", "The Schedule has no future occurrence")
        row = {
            "schedule_id": schedule_id,
            "name": name,
            "source_job_id": source_job_id,
            "rule_json": json.dumps(rule, ensure_ascii=False, sort_keys=True),
            "timezone": rule["timezone"],
            "enabled": 1,
            "overlap_policy": overlap_policy,
            "missed_policy": missed_policy,
            "missed_grace_seconds": grace,
            "starts_at_utc": self._parse_datetime(payload.get("starts_at_utc"), "starts_at_utc"),
            "ends_at_utc": self._parse_datetime(payload.get("ends_at_utc"), "ends_at_utc"),
            "input_root": str(snapshot.root),
            "output_root": str(output_root),
            "retention_json": json.dumps(retention, ensure_ascii=False, sort_keys=True),
            "next_run_at_utc": occurrences[0].instant_utc.isoformat(timespec="seconds"),
            "last_occurrence_key": None,
        }
        self.db.create_schedule(row)
        return self._public(row)

    def preview(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        rule = validate_rule(payload.get("rule"))
        after_value = payload.get("after_utc")
        if after_value:
            try:
                after = datetime.fromisoformat(str(after_value))
            except ValueError:
                raise RelayError("INVALID_REQUEST", "after_utc must be an ISO datetime") from None
            if after.tzinfo is None:
                after = after.replace(tzinfo=UTC)
        else:
            after = datetime.now(UTC)
        occurrences = next_occurrences(rule, after, limit=int(payload.get("limit", 5)))
        return [
            {
                "occurrence_key": item.occurrence_key,
                "utc": item.instant_utc.isoformat(timespec="seconds"),
                "local": item.local_time.isoformat(timespec="minutes"),
                "timezone": rule["timezone"],
            }
            for item in occurrences
        ]

    def list(self) -> list[dict[str, Any]]:
        return [self._public(row) for row in self.db.list_schedules()]

    def show(self, schedule_id: str) -> dict[str, Any]:
        return self._public(self._require(schedule_id))

    def runs(self, schedule_id: str) -> list[dict[str, Any]]:
        self._require(schedule_id)
        return self.db.list_schedule_runs(schedule_id)

    def pause(self, schedule_id: str) -> dict[str, Any]:
        schedule = self._require(schedule_id)
        if not schedule["enabled"]:
            raise RelayError("SCHEDULE_ALREADY_PAUSED", f"Schedule is already paused: {schedule_id}")
        self.db.update_schedule(schedule_id, enabled=0)
        return self.show(schedule_id)

    def resume(self, schedule_id: str) -> dict[str, Any]:
        schedule = self._require(schedule_id)
        if schedule["enabled"]:
            raise RelayError("SCHEDULE_ALREADY_ACTIVE", f"Schedule is already active: {schedule_id}")
        self.db.update_schedule(schedule_id, enabled=1)
        return self.show(schedule_id)

    def run_now(self, schedule_id: str) -> dict[str, Any]:
        schedule = self._require(schedule_id)
        run_id = new_job_id()
        now = datetime.now(UTC)
        local = now.astimezone(ZoneInfo(schedule["timezone"]))
        row = {
            "run_id": run_id,
            "occurrence_key": f"manual:{run_id}",
            "scheduled_for_utc": now.isoformat(timespec="seconds"),
            "scheduled_for_local": local.isoformat(timespec="minutes"),
            "trigger_type": "manual",
            "status": "PLANNED",
        }
        self.db.insert_schedule_run(schedule_id, row)
        return self.db.get_schedule_run(run_id) or row

    def update(self, schedule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        schedule = self._require(schedule_id)
        changes: dict[str, Any] = {}
        if "name" in payload:
            name = str(payload["name"]).strip()
            if not name:
                raise RelayError("INVALID_REQUEST", "Schedule name is required")
            changes["name"] = name
        if "rule" in payload:
            rule = validate_rule(payload["rule"])
            next_item = next_occurrences(rule, datetime.now(UTC), limit=1)
            if not next_item:
                raise RelayError("SCHEDULE_RULE_INVALID", "The Schedule has no future occurrence")
            changes.update(
                {
                    "rule_json": json.dumps(rule, ensure_ascii=False, sort_keys=True),
                    "timezone": rule["timezone"],
                    "next_run_at_utc": next_item[0].instant_utc.isoformat(timespec="seconds"),
                }
            )
        for key in ("overlap_policy", "missed_policy"):
            if key in payload:
                if payload[key] not in ({"skip", "queue"} if key == "overlap_policy" else {"skip", "catch_up"}):
                    raise RelayError("INVALID_REQUEST", f"Invalid {key}")
                changes[key] = payload[key]
        if "retention" in payload:
            changes["retention_json"] = json.dumps(self._parse_retention(payload["retention"]), sort_keys=True)
        if not changes:
            return self._public(schedule)
        self.db.update_schedule(schedule_id, **changes)
        return self.show(schedule_id)

    def delete(self, schedule_id: str) -> bool:
        self._require(schedule_id)
        self.db.update_schedule(schedule_id, enabled=0, deleted_at=utc_now())
        return True
