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
