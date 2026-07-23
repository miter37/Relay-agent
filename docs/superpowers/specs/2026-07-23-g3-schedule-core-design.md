# G3 Schedule Core Design

**Date:** 2026-07-23  
**Release:** Relay 0.9.0  
**Status:** Approved for implementation

## Goal

Add reliable, timezone-aware Schedule support to Relay so a replayable successful Job can be registered from the CLI, executed by the daemon at deterministic times, and stored in unique output folders without duplicate occurrences or unsafe path inheritance.

G3 includes the daemon, SQLite, scheduling API, CLI, snapshot handling, and Schedule-specific retention. Schedule GUI screens are intentionally deferred to G4.

## Scope

Included:

- Daily, Weekly, Monthly, Every N days, and One time rules.
- Multiple times per day where the rule supports times.
- IANA timezone handling with Python `zoneinfo`.
- DST nonexistent-time skip and ambiguous-time first-occurrence policies.
- Next-run and next-five-run preview using the same rule implementation as runtime.
- Atomic occurrence claiming with a unique `(schedule_id, occurrence_key)` constraint.
- Overlap policies: skip or queue.
- Missed-run policies: skip or one grace-window catch-up occurrence.
- Start/end bounds and enabled/paused state.
- Replayable request and attachment snapshots under `RELAY_HOME`.
- Safe scheduled Job creation with `caller=schedule` and `submitted_via=schedule`.
- Unique Schedule output directories and output-root validation.
- Schedule run history linked to normal Job history.
- Schedule-specific retention independent from ordinary Job workspace cleanup.
- CLI and token-authenticated `/v1/schedules` daemon endpoints.
- Version `0.9.0`, release notes, migration tests, and cross-platform regression tests.

Excluded from G3:

- Schedule GUI creation, editing, listing, and detail screens. G4 consumes the API.
- Queued Job editing.
- Cloud or multi-user scheduling.
- Sub-minute schedules.
- Automatic cancellation of an active occurrence when a new occurrence arrives.
- External scheduler dependencies.

## Architecture

The existing daemon Job scheduler continues to execute queued Jobs. Schedule orchestration is a separate layer that creates ordinary queued Jobs and never executes worker adapters directly.

```text
CLI / future GUI
        │ token-authenticated API
        ▼
ScheduleService ─── ScheduleRuleCalculator
        │                    │
        │                    └─ preview and runtime share the same calculation
        ▼
SQLite schedules + schedule_runs
        │ atomic occurrence claim
        ▼
ScheduleRuntimeLoop
        │ safe request clone + unique output paths
        ▼
RelayEngine.queue_scheduled(...)
        │
        ▼
existing Job runner → normal Job history/result/artifacts/events
```

The implementation is split into focused modules:

- `relay/schedules/rules.py`: immutable rule validation and timezone-aware occurrence calculation.
- `relay/schedules/snapshots.py`: task and attachment materialization, hashes, manifests, and safe cleanup markers.
- `relay/schedules/service.py`: Schedule lifecycle, eligibility, safe request cloning, and output-path construction.
- `relay/schedules/runtime.py`: due polling, atomic claims, overlap/missed policies, and Job/run linking.
- `relay/schedules/retention.py`: Schedule output retention and safe deletion of Relay-owned run folders.
- `relay/schedules/__init__.py`: public schedule-domain exports.

The existing Job execution loop is kept behaviorally compatible. If its class is moved out of `relay/daemon.py`, `RelayDaemon.scheduler` remains an alias for compatibility with existing tests and callers.

## Data model and migration

`Database.CURRENT_SCHEMA_VERSION` advances from 1 to 2. Migration is transactional, backed up like the existing migration path, and idempotent.

### `schedules`

```text
schedule_id TEXT PRIMARY KEY
name TEXT NOT NULL
source_job_id TEXT NOT NULL REFERENCES jobs(job_id)
rule_json TEXT NOT NULL
timezone TEXT NOT NULL
enabled INTEGER NOT NULL DEFAULT 1
deleted_at TEXT
overlap_policy TEXT NOT NULL DEFAULT 'skip'
missed_policy TEXT NOT NULL DEFAULT 'skip'
missed_grace_seconds INTEGER NOT NULL DEFAULT 43200
starts_at_utc TEXT
ends_at_utc TEXT
input_root TEXT NOT NULL
output_root TEXT NOT NULL
retention_json TEXT NOT NULL
next_run_at_utc TEXT
last_occurrence_key TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
```

### `schedule_runs`

```text
run_id TEXT PRIMARY KEY
schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE CASCADE
occurrence_key TEXT NOT NULL
scheduled_for_utc TEXT NOT NULL
scheduled_for_local TEXT NOT NULL
trigger_type TEXT NOT NULL
status TEXT NOT NULL
job_id TEXT REFERENCES jobs(job_id)
output_path TEXT
artifact_path TEXT
error_code TEXT
error_message TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
UNIQUE(schedule_id, occurrence_key)
```

Allowed values are validated at the service boundary:

- `trigger_type`: `scheduled`, `catch_up`, `manual`.
- `status`: `PLANNED`, `SKIPPED`, `QUEUED`, `FAILED`, `COMPLETED`, `PARTIAL`, `CANCELLED`.
- `overlap_policy`: `skip`, `queue`.
- `missed_policy`: `skip`, `catch_up`.
- retention mode: `days`, `latest_runs`, `forever`.

The migration must preserve every existing Job, Attempt, Artifact, Event, and capability-audit row. New tables are empty for migrated databases.

## Schedule rules and time semantics

Rule JSON uses these canonical shapes:

```json
{"type":"daily","times":["09:00","13:00"],"timezone":"Asia/Seoul"}
{"type":"weekly","weekdays":[1,3,5],"times":["07:00"],"timezone":"Asia/Seoul"}
{"type":"monthly","month_days":[1,15,28],"times":["09:00"],"missing_day_policy":"skip","timezone":"Asia/Seoul"}
{"type":"n_days","interval_days":3,"anchor_date":"2026-07-23","times":["09:00"],"timezone":"Asia/Seoul"}
{"type":"once","run_at_local":"2026-08-03T10:30:00","timezone":"Asia/Seoul"}
```

`rules.py` exposes a single calculation contract used by both preview and runtime:

```python
next_occurrences(rule: dict[str, Any], after_utc: datetime, limit: int = 5) -> list[Occurrence]
```

`Occurrence` contains a canonical UTC instant, local timezone-aware datetime, and stable `occurrence_key`. The calculator must:

- reject invalid IANA zones, malformed times, empty selections, invalid weekday/month-day values, and non-positive N-day intervals;
- return occurrences strictly after the supplied instant;
- skip nonexistent local times during DST transitions;
- choose the first UTC instant for ambiguous local times;
- avoid duplicate local candidates when multiple rule fields describe the same instant;
- honor start/end bounds and deactivate a one-time Schedule after its occurrence is claimed.

The daemon loop waits using monotonic/event timing, but all persisted schedule times are UTC ISO-8601 values.

## Snapshot and safe request cloning

Schedule creation is allowed only when the source Job is:

- `COMPLETED` with internal result status `complete`;
- replayable with a non-empty saved request;
- backed by a materializable task text or task file;
- backed by existing attachment sources;
- associated with an existing Agent definition;
- not deleted or being removed.

Creation copies the task into:

```text
<RELAY_HOME>/schedule-inputs/<schedule_id>/request.md
```

Attachments are copied below the same directory with normalized filenames. `attachments.json` records source path, stored path, byte size, and SHA-256. Copying is staged in a temporary sibling directory and renamed atomically after all hashes and the manifest are complete. Symlinks, path traversal, duplicate filenames, and configured size limits are rejected.

Each occurrence builds a fresh `JobRequest` from the snapshot. It forcibly applies:

```text
request_id    = None
force_new     = True
caller        = "schedule"
output_path   = unique schedule run result path
artifact_path = unique schedule run artifact path
workspace     = Relay-managed default workspace
task_file     = schedule snapshot request.md
```

The public `/v1/jobs` endpoint cannot provide `schedule_id` or forge a Schedule run link. A private engine method accepts an internal Schedule context and writes the Job row with `schedule_id` and `scheduled_for` atomically before it becomes visible to the Job runner.

## Runtime and atomic claim

`ScheduleRuntime` polls active schedules at a bounded interval and recalculates due state on daemon startup. For each candidate occurrence it performs one SQLite transaction:

1. Re-read the active Schedule and its current `next_run_at_utc`.
2. Insert a `PLANNED` row with the unique `occurrence_key`.
3. If the insert conflicts, another loop already claimed it; stop processing that occurrence.
4. Apply overlap policy against active Jobs for the same Schedule.
5. Record `SKIPPED` when policy says skip.
6. Otherwise build the safe request and unique output directory.
7. Queue the normal Job and update the run row to `QUEUED` with `job_id`.
8. Advance `next_run_at_utc` using the shared rule calculator.
9. Commit. On a request/snapshot/path failure, record a retryable or terminal error without creating a dangling Job.

The transaction must not hold a SQLite write lock while copying files or invoking workers. The claim row prevents duplicate work; a second short transaction links the queued Job and advances the Schedule after preflight succeeds. Failure states are explicit and retried according to the runtime policy.

Active overlap statuses are:

```text
QUEUED, PREPARING, RUNNING, VALIDATING, DELIVERING, CANCEL_REQUESTED
```

`skip` records a skipped run. `queue` creates the occurrence as a normal waiting Job.

For missed runs, `skip` advances through all missed occurrences. `catch_up` creates only the newest missed occurrence when it is within the default 12-hour grace window and labels it `trigger_type=catch_up`.

## Output and retention

Every Schedule occurrence receives a unique directory under the configured Relay Schedule output root or a validated user-selected directory:

```text
<root>/<YYYY-MM-DD_HHMM><UTC-offset>_<short-run-id>/
├── result.json or result.txt
├── relay-receipt.json
└── artifacts/
```

The root itself must be a directory. The user-provided root is never used as a fixed result filename, and the Schedule never inherits the source Job's external output/workspace paths. All resolved paths remain within the configured or selected root; symlink escapes are rejected.

The existing Engine delivery/validation flow remains authoritative. The Schedule layer supplies unique final paths and records them in `schedule_runs`; it does not duplicate result validation.

Schedule retention is independent from ordinary Job workspace cleanup:

- `days`: remove eligible run folders older than N days;
- `latest_runs`: retain the newest N runs;
- `forever`: retain all run folders.

Active runs and at least the newest successful output are protected. Deletion checks the run manifest and removes only Relay-created run directories, never the user-selected root. Failed deletion is retained as a cleanup error for retry. Past Job history remains after Schedule deletion.

## CLI and API

CLI commands are grouped under `relay schedule`:

```text
relay schedule create --from-job JOB_ID --name NAME --type daily --time 09:00 --timezone Asia/Seoul
relay schedule preview --type weekly --weekday 1 --time 09:00 --timezone Asia/Seoul
relay schedule list
relay schedule show SCHEDULE_ID
relay schedule runs SCHEDULE_ID
relay schedule pause SCHEDULE_ID
relay schedule resume SCHEDULE_ID
relay schedule run-now SCHEDULE_ID
relay schedule delete SCHEDULE_ID
```

The exact parser accepts repeated `--time`, `--weekday`, and `--month-day` values and exposes overlap, missed-run, retention, start/end, output-root, and missing-month-day options. Human output is readable; `--machine` returns stable JSON.

Daemon endpoints:

```text
GET    /v1/schedules
POST   /v1/schedules/from-job/{job_id}
GET    /v1/schedules/{schedule_id}
PATCH  /v1/schedules/{schedule_id}
DELETE /v1/schedules/{schedule_id}
POST   /v1/schedules/{schedule_id}/run-now
POST   /v1/schedules/{schedule_id}/pause
POST   /v1/schedules/{schedule_id}/resume
GET    /v1/schedules/{schedule_id}/runs
POST   /v1/schedules/preview
```

Schedule writes require daemon token authorization and normal compatibility gating once GUI consumes them. Error responses use stable codes such as `SCHEDULE_NOT_FOUND`, `SCHEDULE_NOT_ELIGIBLE`, `SCHEDULE_RULE_INVALID`, `SCHEDULE_INPUT_MISSING`, `SCHEDULE_PATH_NOT_ALLOWED`, `SCHEDULE_OCCURRENCE_CONFLICT`, and `SCHEDULE_ALREADY_PAUSED`.

## Versioning and compatibility

- Package version becomes `0.9.0`.
- Existing CLI Job and legacy daemon endpoints remain compatible.
- Existing G2 API schema revision remains readable by the `0.8.0` GUI.
- New Schedule endpoints are additive under `/v1`.
- A daemon that supports G3 but is connected to the G2 GUI continues to expose Schedule data only through safe additive endpoints; Schedule GUI writes remain unavailable until G4.

## Testing strategy

Tests are written before implementation for each vertical slice.

Unit tests:

- every rule type and validation error;
- next-five-run determinism;
- DST nonexistent/ambiguous behavior;
- start/end boundaries and one-time deactivation;
- occurrence-key stability;
- snapshot filename normalization, hash manifest, size limits, symlink/path escape rejection;
- output-folder naming and retention selection.

Database/API tests:

- schema v1 → v2 migration and idempotent reopen;
- source Job eligibility and privacy/replay gates;
- Schedule CRUD and stable error codes;
- atomic duplicate claim from concurrent callers;
- pause/resume/run-now behavior;
- run history and Job linkage;
- schedule caller/submitted_via identity enforcement;
- public Job API cannot forge Schedule linkage.

Runtime/integration tests:

- due Schedule queues a normal Job;
- overlap skip versus queue;
- missed-run skip versus one grace-window catch-up;
- daemon restart recalculates due state;
- one-time Schedule disables after successful claim;
- schedule outputs do not collide across repeated runs;
- Schedule retention does not remove ordinary or active outputs.

Regression gates:

```text
python -m unittest discover -s tests -v
ruff format --check relay tests
ruff check relay tests
python -m compileall -q relay tests
python build_release.py
```

The existing G2 baseline must remain green before each G3 slice is considered complete.

## G3 stop gate

G3 is complete only when:

- deterministic next-run tests pass for all five rule types;
- duplicate occurrence claims produce one Schedule run;
- overlap and missed-run policies are observable in run history;
- a replayable completed Job can be scheduled from the CLI;
- scheduled Jobs appear as normal CLI history rows with Schedule identity;
- task and attachments are copied into immutable input snapshots;
- every run uses a unique output folder;
- Schedule retention is independent from ordinary Job cleanup;
- daemon restart does not duplicate or lose due occurrences under the documented policies;
- all existing tests plus G3 tests pass;
- release metadata is `0.9.0` and the release bundle builds successfully.
