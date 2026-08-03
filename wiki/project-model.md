# Project model

## Actors and entry points

- Humans and external agents submit work through `relay`, the daemon API, or the GUI.
- The daemon authenticates local requests, owns scheduling and maintenance loops, and queues Jobs.
- `RelayEngine` resolves Agent definitions, enforces readiness, supervises processes, validates results, and records history.

## Main components

- Built-in adapters support Claude, Codex, and Antigravity.
- `AgentRegistry` combines built-ins, legacy configured workers, and manifest-backed Agent Apps.
- Agent App manifests live under `Relay Home/config/agent-apps/`; capability specs bind executable version and definition hash.
- Schedules snapshot replayable Job inputs and create ordinary linked Jobs for each occurrence.
- SQLite stores Jobs, attempts, events, artifacts, Artifact lineage, Schedules, Schedule runs, and capability audit history.
- `/health` includes manual-check results for all enabled Agents; the GUI presents unhealthy Agent IDs in its header badge.
- Running Job diagnostics use in-memory supervisor telemetry; manual Check results are persisted as `PROGRESS_CHECKED` events and rendered separately from Agent stdout/stderr.

## Phase 0 domain compatibility

Phase 0 adds the domain vocabulary without renaming or replacing existing stored objects:

| Product term | Current persisted object | Compatibility rule |
|---|---|---|
| Task Run / Run | `jobs` row | `run_id` is an API alias for the existing immutable `job_id`; existing Job IDs remain valid forever. |
| Task | No separate persisted object yet | Phase 0 Jobs are ad-hoc Runs with `task_id = NULL`; Task CRUD begins in Phase 3. |
| Attempt | `attempts` row | One Run may have multiple Attempts because of fallback; each Attempt keeps its worker and audit-bound execution metadata. |
| Artifact | `artifacts` row | New rows receive an immutable external UID, role, and producer Attempt reference; legacy rows remain readable. |
| Project Run | No separate persisted object yet | Not introduced in Phase 0. |
| Routine | `schedules` and `schedule_runs` | Existing Schedule tables remain canonical; Routine is a future product alias, not a table rename. |

The database column names and foreign keys remain unchanged. In particular, `jobs.job_id` remains the storage key referenced by `schedules.source_job_id` and `schedule_runs.job_id`; API and GUI terminology may use Run, but storage compatibility does not depend on a rename.

A Run records both **why it ran** (`trigger_type`: manual, api, schedule, or rerun) and **where it was submitted** (`submitted_via`: cli, gui, hermes, schedule, or legacy). These fields are intentionally separate. The immutable `task_snapshot_json` records the normalized execution definition at creation time; the existing `request_json` remains the replayable request payload for backward-compatible reruns.

## Data flow

```text
CLI / GUI / external caller
        ↓
authenticated daemon API
        ↓
Job queue → Agent registry → verified adapter → supervised process
        ↓
result and artifact validation → SQLite history and delivered outputs
```

Phase 1 Artifact inputs are selected by immutable Artifact UID and copied into a Relay Home snapshot before execution. A consumer Run stores its canonical input manifest and `artifact_lineage` rows; the worker receives only the snapshot copy, not an arbitrary source path.

The synchronous CLI path uses the same engine and validation contracts without requiring the daemon.

For interactive file-writing Jobs, `target_path` identifies the real Working folder while `artifact_path` remains
the Relay-managed copy destination. Agents edit an isolated `target/` copy; Relay applies its verified delta to the
real folder and copies changed/created files to artifacts. Target-writing Jobs are not Schedule-eligible.
