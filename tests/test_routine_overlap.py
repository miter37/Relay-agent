"""Routine overlap policies decide what happens when an occurrence is due while a Run is still in flight."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.routines.runtime import RoutineRuntime
from relay.routines.service import RoutineService


class RoutineOverlapPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = RoutineService(self.config, self.db, self.engine)
        self.runtime = RoutineRuntime(self.config, self.db, self.engine, self.service)
        self.task = self.engine.create_task(TaskSpec(name="daily", instructions="do daily work"))

    def tearDown(self):
        self.runtime.stop()
        self.temp.cleanup()

    def _routine(self, overlap: str) -> dict:
        routine = self.service.create_routine(
            {
                "name": f"routine-{overlap}",
                "target_type": "task",
                "target_id": self.task["task_id"],
                "rule": {"type": "daily", "times": ["00:00"]},
                "timezone": "UTC",
                "overlap_policy": overlap,
                "missed_policy": "replay_all",
            }
        )
        self._make_due(routine["routine_id"])
        return self.db.get_routine(routine["routine_id"])

    def _make_due(self, routine_id: str) -> None:
        """Point the Routine at a past occurrence so the tick has work to do.

        The rule fires daily at 00:00 UTC, so yesterday's midnight is always in the
        past regardless of when the suite runs.
        """
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        due = midnight - timedelta(days=1)
        self.db.update_routine(routine_id, next_run_at_utc=due.isoformat(timespec="seconds"))

    def _in_flight_run(self, routine_id: str) -> str:
        """Create a routine Run that is still running, backed by a real queued job."""
        job, _, _ = self.engine.run_task(self.task["task_id"], queued=True, submitted_via="routine", caller="service")
        run_id = f"run-{routine_id}"
        self.db.claim_routine_occurrence(
            routine_id,
            {
                "run_id": run_id,
                "occurrence_key": "prior",
                "scheduled_for_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "scheduled_for_local": datetime.now(UTC).isoformat(timespec="minutes"),
                "trigger_type": "routine",
                "status": "running",
                "target_type": "task",
            },
        )
        self.db.update_routine_run(run_id, task_run_id=job["job_id"], status="running")
        return run_id

    def _next_run_at(self, routine_id: str) -> str | None:
        return self.db.get_routine(routine_id)["next_run_at_utc"]

    def test_skip_drops_the_occurrence_and_advances(self):
        routine = self._routine("skip")
        self._in_flight_run(routine["routine_id"])
        before = self._next_run_at(routine["routine_id"])

        result = self.runtime.tick_once()

        self.assertGreaterEqual(result["skipped"], 1)
        self.assertEqual(result["queued"], 0)
        # The occurrence is abandoned, so the schedule moves on.
        self.assertNotEqual(self._next_run_at(routine["routine_id"]), before)

    def test_queue_holds_the_occurrence_without_advancing(self):
        routine = self._routine("queue")
        self._in_flight_run(routine["routine_id"])
        before = self._next_run_at(routine["routine_id"])

        result = self.runtime.tick_once()

        self.assertGreaterEqual(result["queued_waiting"], 1)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["skipped"], 0)
        # Nothing is lost: the same occurrence is still the next one due.
        self.assertEqual(self._next_run_at(routine["routine_id"]), before)

    def test_queue_dispatches_once_the_previous_run_finished(self):
        routine = self._routine("queue")
        run_id = self._in_flight_run(routine["routine_id"])
        self.db.update_routine_run(run_id, status="completed")
        before = self._next_run_at(routine["routine_id"])

        result = self.runtime.tick_once()

        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["queued_waiting"], 0)
        self.assertNotEqual(self._next_run_at(routine["routine_id"]), before)

    def test_cancel_previous_cancels_the_in_flight_run_then_dispatches(self):
        routine = self._routine("cancel_previous")
        run_id = self._in_flight_run(routine["routine_id"])

        result = self.runtime.tick_once()

        self.assertGreaterEqual(result["cancelled"], 1)
        self.assertEqual(self.db.get_routine_run(run_id)["status"], "cancelled")
        self.assertGreaterEqual(result["queued"], 1)

    def test_allow_parallel_dispatches_alongside_the_in_flight_run(self):
        routine = self._routine("allow_parallel")
        run_id = self._in_flight_run(routine["routine_id"])

        result = self.runtime.tick_once()

        self.assertGreaterEqual(result["queued"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["cancelled"], 0)
        # The earlier Run is left running.
        self.assertEqual(self.db.get_routine_run(run_id)["status"], "running")

    def test_every_declared_overlap_policy_is_handled(self):
        from relay.routines.models import _VALID_OVERLAP

        source = Path("relay/routines/runtime.py").read_text(encoding="utf-8")
        for policy in _VALID_OVERLAP:
            self.assertIn(f'"{policy}"', source, f"overlap policy {policy!r} has no runtime branch")

    def test_routine_editor_offers_exactly_the_policies_the_core_accepts(self):
        # The editor used to hide queue/cancel_previous while they were unimplemented.
        # Offering fewer strands the user; offering more silently misbehaves.
        try:
            from relay.gui.routines import RoutineEditorDialog
        except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
            self.skipTest(f"GUI extra is not installed: {exc}")
        from relay.routines.models import _VALID_OVERLAP

        self.assertEqual(sorted(RoutineEditorDialog._OVERLAP), sorted(_VALID_OVERLAP))

    def test_routine_rule_round_trips(self):
        routine = self._routine("queue")
        self.assertEqual(json.loads(routine["rule_json"])["type"], "daily")


if __name__ == "__main__":
    unittest.main()
