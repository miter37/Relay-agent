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
- SQLite stores Jobs, attempts, events, artifacts, Schedules, Schedule runs, and capability audit history.
- `/health` includes manual-check results for all enabled Agents; the GUI presents unhealthy Agent IDs in its header badge.
- Running Job diagnostics use in-memory supervisor telemetry; manual Check results are persisted as `PROGRESS_CHECKED` events and rendered separately from Agent stdout/stderr.

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

The synchronous CLI path uses the same engine and validation contracts without requiring the daemon.

For interactive file-writing Jobs, `target_path` identifies the real Working folder while `artifact_path` remains
the Relay-managed copy destination. Agents edit an isolated `target/` copy; Relay applies its verified delta to the
real folder and copies changed/created files to artifacts. Target-writing Jobs are not Schedule-eligible.
