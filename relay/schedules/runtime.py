from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..util import new_job_id
from .rules import next_occurrences
from .snapshots import build_scheduled_request, load_snapshot, schedule_output_paths, validate_source_job

_ACTIVE_STATUSES = {"QUEUED", "PREPARING", "RUNNING", "VALIDATING", "DELIVERING", "CANCEL_REQUESTED"}


@dataclass(frozen=True, slots=True)
class _RunOccurrence:
    instant_utc: datetime
    local_time: datetime
    occurrence_key: str


class ScheduleRuntime:
    def __init__(self, config: Config, db: Database, engine: RelayEngine):
        self.config = config
        self.db = db
        self.engine = engine

    def tick(self, now_utc: datetime | None = None) -> dict[str, int]:
        now = (now_utc or datetime.now(UTC)).astimezone(UTC)
        result = {"queued": 0, "skipped": 0, "failed": 0}
        for schedule in self.db.list_schedules():
            self._process_manual(schedule, now, result)
            if schedule.get("enabled") and schedule.get("next_run_at_utc"):
                self._process_due(schedule, now, result)
        return result

    def _process_manual(self, schedule: dict[str, Any], now: datetime, result: dict[str, int]) -> None:
        for run in self.db.list_schedule_runs(schedule["schedule_id"], limit=100):
            if run.get("status") == "PLANNED" and run.get("trigger_type") == "manual":
                self._process_claimed(schedule, run, now, result, advance=False)

    def _due_occurrence(self, schedule: dict[str, Any]) -> Any | None:
        try:
            next_run = datetime.fromisoformat(schedule["next_run_at_utc"]).astimezone(UTC)
            rule = json.loads(schedule["rule_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RelayError("SCHEDULE_RULE_INVALID", "Stored Schedule timing data is invalid.") from exc
        candidates = next_occurrences(rule, next_run - timedelta(microseconds=1), limit=1)
        return candidates[0] if candidates else None

    def _process_due(self, schedule: dict[str, Any], now: datetime, result: dict[str, int]) -> None:
        occurrence = self._due_occurrence(schedule)
        if occurrence is None or occurrence.instant_utc > now:
            return
        overdue = now - occurrence.instant_utc
        policy = schedule.get("missed_policy", "skip")
        grace = timedelta(seconds=int(schedule.get("missed_grace_seconds", 43200)))
        if overdue > grace and policy == "catch_up":
            latest = occurrence
            for _ in range(100):
                next_item = self._next_after(schedule, latest.instant_utc)
                if not next_item or next_item.instant_utc > now:
                    break
                latest = next_item
            self._claim_and_process(schedule, latest, "catch_up", now, result)
            return
        while occurrence and occurrence.instant_utc <= now:
            trigger = "scheduled"
            if overdue > grace and policy == "skip":
                if not self._claim_and_skip(schedule, occurrence, result):
                    break
            else:
                if not self._claim_and_process(schedule, occurrence, trigger, now, result):
                    break
            if not schedule.get("enabled") or not schedule.get("next_run_at_utc"):
                break
            occurrence = self._due_occurrence(schedule)
            if occurrence is None:
                break
            overdue = now - occurrence.instant_utc

    def _next_after(self, schedule: dict[str, Any], instant: datetime) -> Any | None:
        rule = json.loads(schedule["rule_json"])
        values = next_occurrences(rule, instant, limit=1)
        return values[0] if values else None

    def _claim_and_skip(self, schedule: dict[str, Any], occurrence: Any, result: dict[str, int]) -> bool:
        run = {
            "run_id": new_job_id(),
            "occurrence_key": occurrence.occurrence_key,
            "scheduled_for_utc": occurrence.instant_utc.isoformat(timespec="seconds"),
            "scheduled_for_local": occurrence.local_time.isoformat(timespec="minutes"),
            "trigger_type": "scheduled",
            "status": "SKIPPED",
        }
        try:
            self.db.insert_schedule_run(schedule["schedule_id"], run)
        except sqlite3.IntegrityError:
            return False
        result["skipped"] += 1
        self._advance(schedule, occurrence)
        return True

    def _claim_and_process(
        self,
        schedule: dict[str, Any],
        occurrence: Any,
        trigger_type: str,
        now: datetime,
        result: dict[str, int],
    ) -> bool:
        run = {
            "run_id": new_job_id(),
            "occurrence_key": occurrence.occurrence_key,
            "scheduled_for_utc": occurrence.instant_utc.isoformat(timespec="seconds"),
            "scheduled_for_local": occurrence.local_time.isoformat(timespec="minutes"),
            "trigger_type": trigger_type,
            "status": "PLANNED",
        }
        try:
            self.db.insert_schedule_run(schedule["schedule_id"], run)
        except sqlite3.IntegrityError:
            return False
        self._process_claimed(schedule, run, now, result, advance=True)
        return True

    def _process_claimed(
        self,
        schedule: dict[str, Any],
        run: dict[str, Any],
        now: datetime,
        result: dict[str, int],
        *,
        advance: bool,
    ) -> None:
        schedule_id = schedule["schedule_id"]
        if schedule.get("overlap_policy", "skip") == "skip" and self.db.active_jobs_for_schedule(schedule_id):
            self.db.update_schedule_run(run["run_id"], status="SKIPPED")
            result["skipped"] += 1
            if advance:
                self._advance(schedule, self._run_occurrence(run))
            return
        try:
            source = self.db.get_job(schedule["source_job_id"])
            if not source:
                raise RelayError("SCHEDULE_INPUT_MISSING", "The source Job no longer exists.")
            request = validate_source_job(source, self.engine.agent_registry)
            snapshot = load_snapshot(schedule_id, Path(schedule["input_root"]))
            local_time = datetime.fromisoformat(run["scheduled_for_local"])
            output_path, artifact_path = schedule_output_paths(
                self.config,
                schedule_id,
                run["run_id"],
                local_time,
                request.result_format,
                output_root=Path(schedule["output_root"]),
            )
            request = build_scheduled_request(request, snapshot, output_path, artifact_path)
            queued = self.engine.queue_scheduled(
                request,
                schedule_id=schedule_id,
                scheduled_for=run["scheduled_for_utc"],
                output_path=output_path,
                artifact_path=artifact_path,
            )
            self.db.link_schedule_run_job(run["run_id"], queued["job_id"], status="QUEUED")
            result["queued"] += 1
            if advance:
                self._advance(schedule, self._run_occurrence(run))
            elif schedule.get("rule_json"):
                self.db.update_schedule(schedule_id, last_occurrence_key=run["occurrence_key"])
        except RelayError as exc:
            self.db.update_schedule_run(run["run_id"], status="FAILED", error_code=exc.code, error_message=exc.message)
            result["failed"] += 1

    @staticmethod
    def _run_occurrence(run: dict[str, Any]) -> Any:
        return _RunOccurrence(
            datetime.fromisoformat(run["scheduled_for_utc"]).astimezone(UTC),
            datetime.fromisoformat(run["scheduled_for_local"]),
            run["occurrence_key"],
        )

    def _advance(self, schedule: dict[str, Any], occurrence: Any) -> None:
        next_item = self._next_after(schedule, occurrence.instant_utc)
        changes: dict[str, Any] = {"last_occurrence_key": occurrence.occurrence_key}
        if next_item:
            changes["next_run_at_utc"] = next_item.instant_utc.isoformat(timespec="seconds")
        else:
            changes["next_run_at_utc"] = None
            changes["enabled"] = 0
        self.db.update_schedule(schedule["schedule_id"], **changes)
        schedule.update(changes)
