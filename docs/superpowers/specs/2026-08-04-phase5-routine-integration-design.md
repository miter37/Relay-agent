# Phase 5 Routine Integration Design

**Date:** 2026-08-04
**Status:** Approved
**Source:** `docs/relay_product_direction_v1.0.md`, Phase 5 and Routine sections (5.7, 10)

## 1. Goal

Unify Task and Project repeat execution under a single Routine model. A Routine triggers a Task Run or Project Run on a deterministic timezone-aware schedule, records `trigger_type=routine`, and produces ordinary Run objects indistinguishable from manual execution.

This implements **Phase 5** of `docs/relay_product_direction_v1.0.md`.

## 2. Scope

Phase 5 includes:

- Routine CRUD with target_type `task` or `project`.
- Deterministic timezone-aware rule calculation (reuses `relay/schedules/rules.py`).
- Atomic occurrence claiming with `UNIQUE(routine_id, occurrence_key)`.
- Overlap policy (skip, queue, cancel_previous, allow_parallel).
- Missed-run policy (skip, run_once_on_recovery, replay_all).
- Version policy (latest, pinned).
- Routine execution history.
- Notification policy storage (stored, not enforced in MVP; enforcement is Phase 6).
- Daemon restart recovery (no duplicate or lost occurrences).
- Authenticated daemon APIs and machine-readable CLI commands.
- Coexistence with the existing Schedule subsystem (no removal, no breaking change).

Phase 5 excludes:

- Routine GUI (deferred to a later phase, matching the Schedule/Task/Project precedent).
- Notification delivery (webhook, email, Inbox) — stored only; enforcement is Phase 6.
- Semantic input auto-selection — Phase 6.
- Migration tooling that rewrites existing Schedule rows into Routine rows. Schedules keep working unchanged. A future operator-facing conversion helper can be added without blocking Phase 5.

## 3. Relationship to the Existing Schedule Subsystem

The existing `schedules`, `schedule_runs`, `ScheduleService`, and `ScheduleRuntime` remain fully functional and unchanged. They continue to serve Job-based schedules.

Routines are a parallel, additive subsystem that:

- Reuses `relay/schedules/rules.py` verbatim (`validate_rule`, `next_occurrences`, `Occurrence`).
- Introduces a `RoutineService` and `RoutineRuntime` that mirror the Schedule lifecycle but dispatch to Task Run or Project Run instead of replaying a source Job.

This avoids any risk to the already-shipped Schedule behavior while delivering the Routine model.

## 4. Data Model

A schema v8 additive migration creates two tables.

### 4.1 `routines`

```text
routine_id TEXT PRIMARY KEY
name TEXT NOT NULL
target_type TEXT NOT NULL                 -- task | project
target_id TEXT NOT NULL
rule_json TEXT NOT NULL
timezone TEXT NOT NULL
enabled INTEGER NOT NULL DEFAULT 1
deleted_at TEXT
overlap_policy TEXT NOT NULL DEFAULT 'skip'
missed_policy TEXT NOT NULL DEFAULT 'skip'
missed_grace_seconds INTEGER NOT NULL DEFAULT 43200
version_policy TEXT NOT NULL DEFAULT 'latest'
pinned_version INTEGER
input_policy_json TEXT                    -- stored, surfaced, not enforced in MVP
notification_policy_json TEXT             -- stored, surfaced, not enforced in MVP
starts_at_utc TEXT
ends_at_utc TEXT
next_run_at_utc TEXT
last_occurrence_key TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

### 4.2 `routine_runs`

```text
run_id TEXT PRIMARY KEY
routine_id TEXT NOT NULL REFERENCES routines(routine_id) ON DELETE CASCADE
occurrence_key TEXT NOT NULL
scheduled_for_utc TEXT NOT NULL
scheduled_for_local TEXT NOT NULL
trigger_type TEXT NOT NULL                -- routine | manual
status TEXT NOT NULL                      -- pending | running | completed | failed | skipped | cancelled
target_type TEXT NOT NULL
task_run_id TEXT REFERENCES jobs(job_id)
project_run_id TEXT REFERENCES project_runs(project_run_id)
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
UNIQUE(routine_id, occurrence_key)
```

Indices on `routine_runs(routine_id, scheduled_for_utc)`, `routine_runs(task_run_id)`, `routine_runs(project_run_id)`.

### 4.3 Migration

`CURRENT_SCHEMA_VERSION` increments 7 -> 8. The migration is additive and touches no existing column or row. Existing databases reopen cleanly with empty `routines` and `routine_runs` tables.

## 5. Execution Model

`RoutineRuntime.tick(now_utc)` runs in the daemon maintenance loop:

1. Load enabled Routines ordered by `next_run_at_utc`.
2. For each Routine, compute occurrences between `last_occurrence_key` and `now_utc` using `next_occurrences(rule, after_utc, limit)`.
3. Apply the missed-run policy to occurrences older than `missed_grace_seconds`.
4. Atomically claim each due occurrence via `routine_runs` `UNIQUE(routine_id, occurrence_key)` insert.
5. Dispatch each claimed occurrence:
   - `target_type == task`: call `engine.run_task(target_id, queued=True, submitted_via="routine", trigger_type="routine", routine_id=...)`.
   - `target_type == project`: call `engine.project_service.create_project_run(target_id, trigger_type="routine", submitted_via="routine", caller="service")`.
6. Update the `routine_runs` row with the resulting `task_run_id` or `project_run_id`.
7. Update `routines.last_occurrence_key` and `next_run_at_utc`.
8. Apply overlap policy before claiming: check for a non-terminal `routine_runs` row; skip/queue/cancel_previous/allow_parallel accordingly.

Reconciliation reads child Task Run / Project Run status and updates the `routine_runs` status without re-dispatching.

### 5.1 Version policy

- `latest`: resolve the Task or Project definition at dispatch time (the current mutable row).
- `pinned`: resolve the exact `pinned_version`. If that version no longer matches the current row, fail the occurrence with `ROUTINE_VERSION_PIN_INVALID` rather than silently using a different version.

### 5.2 Input policy

`input_policy_json` is stored and surfaced but not enforced in MVP. Phase 6 introduces automatic Artifact selection. For Phase 5, Routines that target Tasks with required Artifact inputs must declare explicit external bindings at Routine creation; otherwise dispatch fails with `ROUTINE_INPUTS_REQUIRED`.

## 6. Policy Validation

Routine create/update rejects:

- `target_type` not in `{task, project}`.
- `target_id` referencing a missing or soft-deleted Task/Project.
- `rule_json` failing `validate_rule`.
- `timezone` not resolvable via `zoneinfo`.
- `overlap_policy` not in `{skip, queue, cancel_previous, allow_parallel}`.
- `missed_policy` not in `{skip, run_once_on_recovery, replay_all}`.
- `version_policy` not in `{latest, pinned}`.
- `pinned_version` present when `version_policy` is `latest`, or absent when `pinned`.
- `starts_at_utc` after `ends_at_utc`.

## 7. Restart Recovery

After daemon restart:

- `pending`/`running` `routine_runs` rows are reconciled by reading their child Task Run / Project Run status.
- Due occurrences without a `routine_runs` row are claimed and dispatched.
- Already-claimed occurrences are never re-dispatched (unique key guard).
- Terminal Routines are not reopened automatically.

## 8. API

Authenticated daemon endpoints:

```text
POST   /v1/routines
GET    /v1/routines
GET    /v1/routines/{routine_id}
POST   /v1/routines/{routine_id}
DELETE /v1/routines/{routine_id}
POST   /v1/routines/{routine_id}/run-now
GET    /v1/routines/{routine_id}/runs
POST   /v1/routines/preview
```

`preview` reuses the Schedule preview endpoint shape (returns occurrence list without persisting).

Stable Phase 5 errors:

```text
ROUTINE_NOT_FOUND
ROUTINE_INVALID
ROUTINE_RULE_INVALID
ROUTINE_TARGET_MISSING
ROUTINE_VERSION_PIN_INVALID
ROUTINE_INPUTS_REQUIRED
ROUTINE_RUN_TERMINAL
```

## 9. CLI

```text
relay routine create --target-type task|project --target-id ID
                     --type daily|weekly|monthly|once|ndays
                     --time 09:00 [--weekday 1] [--month-day 15] [--n-days 3]
                     --timezone Asia/Seoul
                     [--name NAME] [--overlap skip|queue|cancel_previous|allow_parallel]
                     [--missed skip|run_once_on_recovery|replay_all]
                     [--version-policy latest|pinned] [--pinned-version N]
                     [--input NODE:ALIAS=ARTIFACT_UID]...
                     [--starts-at ISO] [--ends-at ISO]
relay routine list
relay routine show <routine_id>
relay routine update <routine_id> [...]
relay routine delete <routine_id>
relay routine run-now <routine_id>
relay routine runs <routine_id>
relay routine preview --type daily --time 09:00 --timezone Asia/Seoul
```

All commands support `--machine` and stable JSON.

## 10. Component Boundaries

```text
relay/routines/models.py
  RoutineSpec, validation, version_policy resolution helpers

relay/routines/service.py
  Routine CRUD, preview, run-now, reconciliation helpers, receipt

relay/routines/runtime.py
  Persistent tick, occurrence claim, dispatch to Task/Project Run

relay/db.py
  Schema v8 and atomic routine DB primitives only

relay/engine.py
  run_task gains routine_id/trigger_type passthrough
  project_service.create_project_run gains trigger_type/routine_id passthrough

relay/api.py / relay/daemon.py / relay/cli.py
  Thin transport and presentation layers
```

## 11. Testing and Acceptance

Testing is divided into:

1. v7-to-v8 migration, idempotent reopen, row preservation, and atomic occurrence claims.
2. RoutineSpec validation, rule reuse, policy validation.
3. Sequential Task-target Routine dispatch, Project-target Routine dispatch, overlap skip, missed-run skip, restart recovery, version pin mismatch, manual run-now.
4. API/CLI schemas and a complete daemon integration flow.

Acceptance flow:

- Create a Task and a Project.
- Create one Routine targeting the Task (daily) and one targeting the Project (weekly).
- Assert both produce ordinary Task Run / Project Run rows with `trigger_type=routine`.
- Assert the resulting Runs are indistinguishable from manually-triggered Runs (same fields, same receipts).
- Assert one Task can carry two Routines (daily + weekly) without collision.
- Assert daemon restart does not duplicate or miss an occurrence.

## 12. Completion Criteria

Phase 5 is complete when:

- One Task or Project can carry multiple Routines.
- Routine execution creates an ordinary Task Run or Project Run.
- Manual execution and Routine execution produce identical Run result structures.
- Overlap and missed-run policies behave as documented.
- Daemon restart does not duplicate or lose occurrences.
- The existing Schedule subsystem continues to work unchanged.
- CLI, daemon API, runtime, service, and DB primitives each have focused tests; full suite, Ruff, and compileall pass.
- The `/health` capabilities list advertises `routine-runtime` without breaking the GUI compatibility floor.

## 13. Open Questions

None at design time. Implementation choices follow existing Schedule and Project conventions.
