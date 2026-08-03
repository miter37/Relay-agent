# Phase 5 Routine Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Routine layer that runs a Task or Project on a deterministic timezone-aware schedule, records `trigger_type=routine`, and produces ordinary Task Run / Project Run objects indistinguishable from manual execution.

**Architecture:** A schema v8 additive migration adds `routines` and `routine_runs`. A `RoutineService` mirrors the Schedule lifecycle but dispatches to Task Run or Project Run via the existing engine. A `RoutineRuntime` reuses `relay/schedules/rules.py` and the ScheduleRuntime tick pattern. The existing Schedule subsystem stays fully functional and unchanged.

**Tech Stack:** Python 3.11+, SQLite, standard library, `unittest`, existing loopback daemon/RPC, Ruff.

## Global Constraints

- Phase 5 covers daemon/API/CLI core only; Routine GUI is deferred (Schedule/Task/Project precedent).
- DB `CURRENT_SCHEMA_VERSION` bumps 7 -> 8; the migration is additive (two new tables) and touches no existing column or row.
- The existing `schedules`, `schedule_runs`, `ScheduleService`, and `ScheduleRuntime` remain fully functional and unchanged. No removal, no breaking change.
- Routines reuse `relay/schedules/rules.py` verbatim (`validate_rule`, `next_occurrences`, `Occurrence`).
- Routine execution creates an ordinary Task Run or Project Run and records `trigger_type=routine` plus `routine_id`.
- `input_policy_json` and `notification_policy_json` are stored and surfaced only; enforcement is Phase 6.
- `failure_policy` is `stop` only in MVP (inherited from Project runtime).
- Routine deletion is soft-delete (`deleted_at`) and never touches existing Runs.
- Every occurrence claim is atomic via `UNIQUE(routine_id, occurrence_key)` and never holds a transaction across Worker execution.
- `relay-receipt.json`, `test_result.json`, `test_task.md` are never staged.
- Version string stays `1.1.0` (Phase 5 is additive; no release cut in this plan).
- Work continues on the current `feat/phase0-domain-compat` branch (Phase 0/1/2/3/4 live here as sequential commits).

---

### Task 1: Schema v8 migration and Routine DB primitives

**Files:**
- Modify: `relay/db.py` (SCHEMA constant, `CURRENT_SCHEMA_VERSION`, migration constants, `migrate()` dispatch, CRUD methods)
- Modify: `tests/test_migrations.py`
- Create: `tests/test_phase5_db.py`

**Interfaces:**
- Produces: `Database.create_routine(row)`, `Database.get_routine(routine_id)`, `Database.list_routines(*, include_deleted=False, limit=200)`, `Database.update_routine(routine_id, **changes)`, `Database.soft_delete_routine(routine_id) -> bool`, `Database.claim_routine_occurrence(routine_id, run_row) -> bool` (atomic), `Database.insert_routine_run(row)`, `Database.get_routine_run(run_id)`, `Database.list_routine_runs(*, routine_id, limit=100)`, `Database.active_runs_for_routine(routine_id) -> list`, `Database.update_routine_run(run_id, **changes)`, `Database.update_routine_run_job(run_id, *, task_run_id=None, project_run_id=None, status)`.
- Produces: `Database.migrate()` handles version 7 -> 8 idempotently.

- [ ] **Step 1: Write the failing DB tests**

Create `tests/test_phase5_db.py`:

```python
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database
from relay.errors import RelayError


class RoutineDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "relay.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_7_to_8_creates_routine_tables(self):
        with sqlite3.connect(self.path) as conn, conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"routines", "routine_runs"} <= tables)

    def test_routine_crud_and_soft_delete(self):
        self.db.create_routine({
            "routine_id": "r-1", "name": "Daily HBM", "target_type": "task", "target_id": "T-1",
            "rule_json": "{}", "timezone": "Asia/Seoul", "enabled": 1,
            "overlap_policy": "skip", "missed_policy": "skip", "missed_grace_seconds": 43200,
            "version_policy": "latest", "next_run_at_utc": None,
        })
        self.assertEqual(self.db.get_routine("r-1")["name"], "Daily HBM")
        self.assertEqual([r["routine_id"] for r in self.db.list_routines()], ["r-1"])
        self.db.update_routine("r-1", name="Renamed")
        self.assertEqual(self.db.get_routine("r-1")["name"], "Renamed")
        self.assertTrue(self.db.soft_delete_routine("r-1"))
        self.assertIsNotNone(self.db.get_routine("r-1")["deleted_at"])
        self.assertEqual(self.db.list_routines(), [])

    def test_claim_routine_occurrence_is_atomic(self):
        self.db.create_routine({
            "routine_id": "r-1", "name": "R", "target_type": "task", "target_id": "T-1",
            "rule_json": "{}", "timezone": "Asia/Seoul", "enabled": 1,
            "overlap_policy": "skip", "missed_policy": "skip", "missed_grace_seconds": 43200,
            "version_policy": "latest", "next_run_at_utc": None,
        })
        run = {"run_id": "rr-1", "occurrence_key": "2026-08-04T00:00",
               "scheduled_for_utc": "2026-08-04T00:00:00+00:00",
               "scheduled_for_local": "2026-08-04T09:00:00+09:00",
               "trigger_type": "routine", "status": "pending", "target_type": "task"}
        self.assertTrue(self.db.claim_routine_occurrence("r-1", run))
        self.assertFalse(self.db.claim_routine_occurrence("r-1", {**run, "run_id": "rr-2"}))
        runs = self.db.list_routine_runs(routine_id="r-1")
        self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add migration coverage in test_migrations.py**

Append a test that builds a v7 db, sets `PRAGMA user_version=7`, reopens, and asserts both `routines` and `routine_runs` tables exist and version is 8.

- [ ] **Step 3: Verify tests fail**

Run: `python -m unittest tests.test_phase5_db tests.test_migrations -v`
Expected: FAIL (no `routines` table).

- [ ] **Step 4: Schema v8 in db.py**

Set `CURRENT_SCHEMA_VERSION = 8`. Append to `SCHEMA` (after `project_step_runs`) and define `MIGRATION_7_TO_8` with the `routines` and `routine_runs` tables described in design spec section 4 plus indices. Update `migrate()` to include the `version == 7` branch and add `MIGRATION_7_TO_8` to the `version == 0` chain and the `version in {1..7}` chain.

- [ ] **Step 5: Add CRUD + claim methods**

Append to `Database` (follow `create_schedule`/`get_schedule` patterns):
- `create_routine(row)`, `get_routine`, `list_routines(*, include_deleted=False, limit=200)`, `update_routine(routine_id, **changes)`, `soft_delete_routine(routine_id) -> bool` (raise `ROUTINE_NOT_FOUND` if missing; return False if already deleted).
- `insert_routine_run(row)`, `get_routine_run(run_id)`, `update_routine_run(run_id, **changes)`, `list_routine_runs(*, routine_id, limit=100)`.
- `claim_routine_occurrence(routine_id, run_row) -> bool`: a single transaction that attempts `INSERT INTO routine_runs` and returns True on success, False on `IntegrityError` (duplicate `occurrence_key`).
- `active_runs_for_routine(routine_id) -> list[dict]`: non-terminal `routine_runs` rows for overlap checks.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m unittest tests.test_phase5_db tests.test_migrations -v`
Commit: `git commit -m "feat: add routine tables and database primitives (phase 5)"`.

### Task 2: RoutineSpec model and validation

**Files:**
- Create: `relay/routines/__init__.py`, `relay/routines/models.py`
- Create: `tests/test_phase5_models.py`

**Interfaces:**
- `RoutineSpec` dataclass with `target_type`, `target_id`, `rule`, `timezone`, `overlap_policy`, `missed_policy`, `missed_grace_seconds`, `version_policy`, `pinned_version`, `input_policy`, `notification_policy`, `starts_at_utc`, `ends_at_utc`, `name`, `enabled`.
- `RoutineSpec.validate(*, task_lookup, project_lookup)` enforcing design spec section 6.
- `RoutineSpec.to_row(routine_id=None) -> dict` for DB persistence.
- `RoutineSpec.from_dict(payload) -> RoutineSpec`.
- `RoutineSpec.canonical_rule(payload) -> str` building the canonical rule JSON from CLI-style args (reuses ScheduleService's canonical rule builder pattern).

- [ ] **Step 1: Write failing tests**

Cover: valid task-target Routine passes; invalid target_type rejected; missing Task rejected (`ROUTINE_TARGET_MISSING`); invalid rule rejected (`ROUTINE_RULE_INVALID`); pinned without version_policy=pinned rejected; project target validated against project_lookup.

- [ ] **Step 2: Implement models**

- `relay/routines/__init__.py` empty.
- `relay/routines/models.py` implementing the dataclass, `from_dict`, `to_row`, `validate` (delegates rule validation to `relay.schedules.rules.validate_rule`, timezone resolution via `zoneinfo`), and `canonical_rule` (mirrors ScheduleService's `_canonical_rule` to keep rule shapes identical between Schedule and Routine).

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase5_models -v`
Commit: `git commit -m "feat: add RoutineSpec model with validation (phase 5)"`.

### Task 3: Engine passthrough for routine_id and trigger_type

**Files:**
- Modify: `relay/engine.py` (`run_task` accepts `routine_id`, `trigger_type`; `create_project_run` passthrough)
- Modify: `relay/projects/service.py` (`create_project_run` accepts `trigger_type`, `submitted_via`, `caller`)
- Create: `tests/test_phase5_engine.py`

**Interfaces:**
- `RelayEngine.run_task(task_id, *, ..., trigger_type=None, routine_id=None)` passes `trigger_type` and stores `routine_id` on the Job row (requires a `routine_id` column on `jobs` — see Task 1; if not added, store in `request_json` or a new `metadata_json` column).
- `ProjectService.create_project_run(project_id, *, trigger_type="manual", submitted_via="cli", caller="human", routine_id=None)` records `trigger_type` and `routine_id` on the `project_runs` row.

- [ ] **Step 1: Add routine_id column (additive) to jobs and project_runs**

Add `routine_id TEXT` to `jobs` and `project_runs` via the v8 migration (extend MIGRATION_7_TO_8 with two `ALTER TABLE` statements). This is additive and safe.

- [ ] **Step 2: Write failing tests**

Cover: `run_task` with `trigger_type="routine", routine_id="r-1"` produces a Job whose `trigger_type` is `routine` and whose `routine_id` is recorded; `create_project_run` with the same passthrough produces a `project_runs` row with `trigger_type=routine`.

- [ ] **Step 3: Implement passthrough**

Thread `trigger_type` and `routine_id` through `run_task` -> `create_job` and through `create_project_run`. Default `trigger_type` stays `manual`/`project` for backward compatibility.

- [ ] **Step 4: Run and commit**

Run: `python -m unittest tests.test_phase5_engine -v`
Commit: `git commit -m "feat: support routine_id and trigger_type passthrough (phase 5)"`.

### Task 4: Routine service (CRUD, preview, run-now, reconcile)

**Files:**
- Create: `relay/routines/service.py`
- Create: `tests/test_phase5_service.py`

**Interfaces:**
- `RoutineService(config, db, engine)` constructor.
- `create_routine(payload) -> dict` validates, resolves target, computes `next_run_at_utc`, stores.
- `update_routine(routine_id, payload) -> dict` mutates row, recomputes `next_run_at_utc`.
- `soft_delete_routine(routine_id) -> bool`.
- `get_routine(routine_id)`, `list_routines(name=None, limit=200)`.
- `preview(payload) -> dict` returns the next N occurrences without persisting (reuses `next_occurrences`).
- `run_now(routine_id) -> dict` claims a manual occurrence and dispatches immediately.
- `reconcile_run(run) -> dict` reads the child Task Run / Project Run status and updates the `routine_runs` row.
- `routine_receipt(routine_id) -> dict` returns Routine metadata + run history.

- [ ] **Step 1: Write failing tests**

Cover: create Routine with valid Task target; preview returns occurrences; run_now creates a Task Run with `trigger_type=routine`; soft_delete preserves history; version_policy=pinned resolves against the current Task version.

- [ ] **Step 2: Implement service**

Mirror `ScheduleService` structure. `run_now` and `reconcile_run` delegate to the runtime's dispatch logic (shared helper) so dispatch stays in one place.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase5_service -v`
Commit: `git commit -m "feat: add Routine service for CRUD and run-now (phase 5)"`.

### Task 5: Routine runtime (persistent tick + daemon wiring)

**Files:**
- Create: `relay/routines/runtime.py`
- Create: `tests/test_phase5_runtime.py`
- Modify: `relay/daemon.py` (wire `RoutineRuntime` into maintenance loop)

**Interfaces:**
- `RoutineRuntime(config, db, engine, service)` with `start()`, `stop()`, `tick(now_utc=None) -> dict`.
- `tick` reuses the ScheduleRuntime tick skeleton: load enabled Routines, compute due occurrences, apply missed_policy, claim atomically, dispatch, advance `last_occurrence_key` and `next_run_at_utc`.
- Dispatch helper shared with `run_now`: resolves target_type, calls `engine.run_task` or `engine.project_service.create_project_run`, updates the `routine_runs` row with the child run id.
- Restart recovery: reconcile non-terminal `routine_runs` by reading child status.

- [ ] **Step 1: Write failing tests**

Cover:
- Task-target Routine dispatches a Task Run with `trigger_type=routine`.
- Project-target Routine dispatches a Project Run with `trigger_type=routine`.
- overlap skip: a second occurrence while one is running is skipped.
- missed_policy skip: an occurrence older than grace is skipped.
- restart does not re-dispatch an already-claimed occurrence.
- version_policy=pinned with a mismatched version fails the occurrence with `ROUTINE_VERSION_PIN_INVALID`.

- [ ] **Step 2: Implement runtime**

Build the runtime by adapting `ScheduleRuntime`. Dispatch helper:
```python
def _dispatch(self, routine, run):
    if routine["target_type"] == "task":
        job, _, _ = self.engine.run_task(
            routine["target_id"], queued=True, submitted_via="routine",
            trigger_type="routine", routine_id=routine["routine_id"],
        )
        self.db.update_routine_run(run["run_id"], task_run_id=job["job_id"], status="running")
    else:  # project
        payload = self.engine.project_service.create_project_run(
            routine["target_id"], trigger_type="routine", submitted_via="routine",
            caller="service", routine_id=routine["routine_id"],
        )
        self.db.update_routine_run(run["run_id"], project_run_id=payload["project_run_id"], status="running")
```

- [ ] **Step 3: Wire into daemon**

In `relay/daemon.py`, alongside the existing `schedule_runtime`, initialize `self.routine_runtime = RoutineRuntime(self.config, self.db, self.engine)` and `self.routine_service = RoutineService(self.config, self.db, self.engine)`. Start/stop with the maintenance loop. Add `"routine-runtime"` to the `/health` capabilities list.

- [ ] **Step 4: Run and commit**

Run: `python -m unittest tests.test_phase5_runtime -v`
Commit: `git commit -m "feat: add persistent Routine runtime (phase 5)"`.

### Task 6: API functions

**Files:**
- Modify: `relay/api.py`
- Create: `tests/test_phase5_api.py`

**Interfaces:**
- `list_routines(engine)`, `create_routine(engine, payload)`, `get_routine(engine, routine_id)`, `update_routine(engine, routine_id, payload)`, `delete_routine(engine, routine_id)`, `run_routine_now(engine, routine_id)`, `routine_runs(engine, routine_id)`, `preview_routine(engine, payload)`.

- [ ] **Step 1: Tests** (mirror Phase 3/4 API tests via daemon RPC).

- [ ] **Step 2: Implement** — thin wrappers returning `{"ok": True, ...}`.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase5_api -v`
Commit: `git commit -m "feat: expose routine API functions (phase 5)"`.

### Task 7: Daemon routes

**Files:**
- Modify: `relay/daemon.py`
- Modify: `tests/test_phase5_api.py` (extend with daemon route tests)

**Routes:**
- `GET/POST /v1/routines`, `GET/POST/DELETE /v1/routines/{id}`, `POST /v1/routines/{id}/run-now`, `GET /v1/routines/{id}/runs`, `POST /v1/routines/preview`.
- Verify `/health` advertises `routine-runtime`.

- [ ] **Step 1: Tests** — boot daemon, assert route payloads and 404 on missing Routine.

- [ ] **Step 2: Implement** — wire routes to API functions. Follow the existing schedule route pattern.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase5_api -v`
Commit: `git commit -m "feat: route routine daemon endpoints (phase 5)"`.

### Task 8: CLI commands

**Files:**
- Modify: `relay/cli.py`
- Create: `tests/test_phase5_cli.py`

**Commands:**
- `relay routine {create|list|show|update|delete|run-now|runs|preview}`.

Follow `_add_schedule_parsers` pattern (repeated `--time`, `--weekday`, `--month-day`, policy args). Add `"routine"` to `COMMANDS`. All commands accept `--machine`.

- [ ] **Step 1: Tests** — parse each command path and assert namespace fields.

- [ ] **Step 2: Implement** — add `_add_routine_parsers` and `_routine_cli_request`. Dispatch from `main()` via `elif args.command == "routine": ...`.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase5_cli -v`
Commit: `git commit -m "feat: add routine CLI commands (phase 5)"`.

### Task 9: Integration coverage + coexistence with Schedule + restart safety

**Files:**
- Create: `tests/test_phase5_integration.py`

- [ ] **Step 1: Acceptance test**

- Create a Task and a Project.
- Create two Routines targeting the same Task (daily + weekly) and verify both coexist.
- Create one Routine targeting the Project (weekly).
- Assert each Routine dispatches ordinary Task Run / Project Run rows with `trigger_type=routine`.
- Assert the resulting Runs have the same shape as manually-triggered Runs (trigger_type differs, all other fields consistent).
- Assert the existing Schedule subsystem still works (create a Schedule from a completed Job and verify it dispatches unchanged).

- [ ] **Step 2: Restart safety test**

- Run a Routine to completion.
- Recreate a `RoutineRuntime` over the same DB.
- Assert no duplicate `routine_runs` rows.

- [ ] **Step 3: Run + commit**

Run: `python -m unittest tests.test_phase5_integration -v`
Commit: `git commit -m "test: phase 5 acceptance, coexistence, and restart coverage"`.

### Task 10: Final verification

- [ ] **Step 1: Full verification**

Run:
```
python -m unittest discover -s tests -v
python -m ruff format --check relay tests
python -m ruff check relay tests
python -m compileall -q relay tests
git diff --check
```
Expected: all green; Phase 0/1/2/3/4 and all G-series suites pass.

- [ ] **Step 2: Confirm scope and user files**

Run `git status --short --branch` and `git ls-files --others --exclude-standard`.
Expected: `relay-receipt.json`, `test_result.json`, `test_task.md` remain untracked; no unintended files staged.

- [ ] **Step 3: Update log.md and final commit**

Append a `log.md` entry for Phase 5 and commit if anything extra was added.

## Stop Gate

- One Task or Project can carry multiple Routines.
- Routine execution creates an ordinary Task Run or Project Run with `trigger_type=routine` and `routine_id` recorded.
- Manual execution and Routine execution produce identical Run result structures.
- Overlap and missed-run policies behave as documented.
- Daemon restart does not duplicate or lose occurrences.
- The existing Schedule subsystem continues to work unchanged.
- CLI, daemon API, runtime, service, and DB primitives each have focused tests; the full suite, Ruff, and compileall pass.
- The `/health` capabilities list advertises `routine-runtime` without breaking the GUI compatibility floor.
- `relay-receipt.json`, `test_result.json`, and `test_task.md` are never staged.
