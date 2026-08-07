from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Config
from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..schedules.rules import Occurrence, next_occurrences
from ..util import new_job_id
from .service import RoutineService

logger = logging.getLogger(__name__)


_TERMINAL = {"completed", "failed", "cancelled", "skipped"}


class RoutineRuntime:
    def __init__(
        self, config: Config, db: Database, engine: RelayEngine, service: RoutineService, *, tick_seconds: float = 1.0
    ):
        self.config = config
        self.db = db
        self.engine = engine
        self.service = service
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(target=self._loop, name="routine-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def wake(self) -> None:
        self._wake.set()

    def tick_once(self, now_utc: datetime | None = None) -> dict[str, int]:
        now = (now_utc or datetime.now(UTC)).astimezone(UTC)
        result = {
            "queued": 0,
            "skipped": 0,
            "failed": 0,
            "reconciled": 0,
            # overlap=queue held this many occurrences for a later tick
            "queued_waiting": 0,
            # overlap=cancel_previous cancelled this many in-flight Runs
            "cancelled": 0,
        }
        self._reconcile_active_runs(result)
        for routine in self.db.list_routines(limit=200):
            try:
                self._process_routine(routine, now, result)
            except Exception as exc:
                logger.exception("routine tick error for %s: %s", routine.get("routine_id"), exc)
                result["failed"] += 1
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:
                logger.exception("routine runtime loop error: %s", exc)
            self._wake.wait(self.tick_seconds)
            self._wake.clear()

    def _reconcile_active_runs(self, result: dict[str, int]) -> None:
        for run in self.db.list_routine_runs(limit=200):
            if run["status"] not in {"pending", "running"}:
                continue
            self.service.reconcile_run(run["run_id"])
            result["reconciled"] += 1

    def _process_routine(self, routine: dict[str, Any], now: datetime, result: dict[str, int]) -> dict[str, int]:
        if not routine.get("enabled") or routine.get("deleted_at"):
            return result
        try:
            rule = __import__("json").loads(routine["rule_json"])
        except Exception:
            return result
        rule.setdefault("timezone", routine["timezone"])
        starts = self._parse_dt(routine.get("starts_at_utc"))
        ends = self._parse_dt(routine.get("ends_at_utc"))
        next_due = self._parse_dt(routine.get("next_run_at_utc"))
        if next_due is None or next_due > now:
            return result
        occurrences = next_occurrences(
            rule,
            next_due - timedelta(microseconds=1),
            limit=100,
            starts_at_utc=starts,
            ends_at_utc=ends,
        )
        if not occurrences:
            return result
        # Dispatch only occurrences that are due. The stored next_run_at_utc is the
        # inclusive catch-up boundary; future occurrences remain untouched.
        pending = [occ for occ in occurrences if occ.instant_utc <= now]
        if not pending:
            return result
        if routine.get("missed_policy", "skip") == "run_once_on_recovery" and len(pending) > 1:
            pending = [pending[-1]]
        # Apply overlap policy
        overlap = routine.get("overlap_policy", "skip")
        active = self.db.active_runs_for_routine(routine["routine_id"]) if overlap != "allow_parallel" else []
        if active:
            if overlap == "skip":
                self._advance(routine, pending[-1])
                result["skipped"] += len(pending)
                return result
            if overlap == "queue":
                # Hold this occurrence without advancing so the next tick retries it
                # once the in-flight Run finishes. Order is preserved because
                # next_run_at_utc still points at the oldest pending occurrence.
                result["queued_waiting"] += len(pending)
                return result
            if overlap == "cancel_previous":
                self._cancel_active_runs(active, result)
        if overlap == "queue":
            # Dispatch one occurrence per tick so queued occurrences run in order
            # instead of bursting all at once when the previous Run finishes.
            pending = pending[:1]
        for occ in pending:
            trigger = "routine"
            grace = timedelta(seconds=int(routine.get("missed_grace_seconds", 43200)))
            overdue = now - occ.instant_utc
            policy = routine.get("missed_policy", "skip")
            if overdue > grace and policy == "skip":
                self._claim_skipped(routine, occ)
                result["skipped"] += 1
                self._advance(routine, occ)
                continue
            self._claim_and_process(routine, occ, trigger, result)
            self._advance(routine, occ)
        return result

    def _cancel_active_runs(self, active: list[dict[str, Any]], result: dict[str, int]) -> None:
        """Cancel in-flight Runs so a newer occurrence can take over.

        Cancellation is best effort: a Run that finished between the query and
        here is simply left alone rather than failing the whole tick.
        """
        for run in active:
            try:
                if run.get("task_run_id"):
                    self.engine.cancel(run["task_run_id"])
                elif run.get("project_run_id"):
                    self.engine.project_service.cancel_project_run(run["project_run_id"])
            except RelayError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("cancel_previous failed for routine run %s: %s", run.get("run_id"), exc)
            self.db.update_routine_run(run["run_id"], status="cancelled")
            result["cancelled"] += 1

    def _claim_skipped(self, routine: dict[str, Any], occ: Occurrence) -> None:
        run = self._build_run(routine, occ, status="skipped")
        self.db.claim_routine_occurrence(routine["routine_id"], run)

    def _dispatch_claim(self, routine: dict[str, Any], occ: Occurrence) -> str | None:
        run = self._build_run(routine, occ, status="pending")
        if not self.db.claim_routine_occurrence(routine["routine_id"], run):
            return None
        return run["run_id"]

    def _dispatch(self, routine: dict[str, Any], run_id: str) -> None:
        try:
            if routine.get("version_policy") == "pinned":
                target = (
                    self.db.get_task(routine["target_id"])
                    if routine["target_type"] == "task"
                    else self.db.get_project(routine["target_id"])
                )
                if not target or int(target["version"]) != int(routine["pinned_version"]):
                    raise RelayError(
                        "ROUTINE_VERSION_PIN_INVALID",
                        f"Pinned version {routine['pinned_version']} is not current for {routine['target_id']}",
                    )
            if routine["target_type"] == "task":
                job, _, _ = self.engine.run_task(
                    routine["target_id"],
                    queued=True,
                    submitted_via="routine",
                    trigger_type="routine",
                    routine_id=routine["routine_id"],
                    caller="service",
                )
                self.db.update_routine_run(run_id, task_run_id=job["job_id"], status="running")
            else:
                project_run = self.engine.project_service.create_project_run(
                    routine["target_id"],
                    trigger_type="routine",
                    submitted_via="routine",
                    caller="service",
                    routine_id=routine["routine_id"],
                )
                self.db.update_routine_run(run_id, project_run_id=project_run["project_run_id"], status="running")
        except RelayError as exc:
            self.db.update_routine_run(run_id, status="failed", error_code=exc.code, error_message=exc.message)
        except Exception as exc:
            logger.exception("dispatch error for routine %s: %s", routine["routine_id"], exc)
            self.db.update_routine_run(
                run_id, status="failed", error_code="ROUTINE_DISPATCH_FAILED", error_message=str(exc)
            )

    def _claim_and_process(
        self, routine: dict[str, Any], occ: Occurrence, trigger_type: str, result: dict[str, int]
    ) -> bool:
        run_id = self._dispatch_claim(routine, occ)
        if not run_id:
            return False
        self._dispatch(routine, run_id)
        # The actual claim in dispatch is best-effort; if dispatch succeeded status=running.
        if self.db.get_routine_run(run_id)["status"] == "failed":
            result["failed"] += 1
        else:
            result["queued"] += 1
        return True

    def _build_run(self, routine: dict[str, Any], occ: Occurrence, *, status: str) -> dict[str, Any]:
        return {
            "run_id": new_job_id(),
            "occurrence_key": occ.occurrence_key,
            "scheduled_for_utc": occ.instant_utc.isoformat(timespec="seconds"),
            "scheduled_for_local": occ.local_time.isoformat(timespec="minutes"),
            "trigger_type": "routine",
            "status": status,
            "target_type": routine["target_type"],
        }

    def _advance(self, routine: dict[str, Any], occ: Occurrence) -> None:
        # Calculate next occurrence strictly after the current one
        try:
            rule = __import__("json").loads(routine["rule_json"])
            rule.setdefault("timezone", routine["timezone"])
            starts = self._parse_dt(routine.get("starts_at_utc"))
            ends = self._parse_dt(routine.get("ends_at_utc"))
            next_items = next_occurrences(rule, occ.instant_utc, limit=1, starts_at_utc=starts, ends_at_utc=ends)
            next_run = next_items[0].instant_utc.isoformat(timespec="seconds") if next_items else None
        except Exception:
            next_run = None

        self.db.update_routine(
            routine["routine_id"],
            last_occurrence_key=occ.occurrence_key,
            next_run_at_utc=next_run,
        )

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).astimezone(UTC)
        except Exception:
            return None
