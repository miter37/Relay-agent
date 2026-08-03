# Phase 4 Project MVP Design

**Date:** 2026-08-04  
**Status:** Approved  
**Source:** `docs/relay_product_direction_v1.0.md`, Phase 4 and Project sections

## 1. Goal

Add a persistent Project execution layer that connects registered Tasks through explicit Artifact bindings, runs independent nodes in parallel, joins dependent results, preserves complete execution history, and safely resumes after daemon restart.

Phase 4 covers the daemon/API/CLI core. A visual Flow GUI is deferred to a later phase, following the Schedule-core and Task-core sequencing used previously.

## 2. Scope

Phase 4 includes:

- Project CRUD and soft-mutation versioning.
- Directed acyclic Task-node definitions.
- Sequential execution, simple fan-out parallelism, and fan-in joins.
- Explicit Artifact-role-to-input-alias bindings.
- Persistent Project Runs and per-node execution state.
- Failure-stop behavior and failed-step/from-node/full reruns.
- Final Artifact selection and Project Run receipts.
- Daemon restart recovery.
- Authenticated daemon APIs and machine-readable CLI commands.

Phase 4 excludes:

- Flow GUI or visual DAG editor.
- Human checkpoints, approval states, and externally supplied human edits.
- Conditional expressions, loops, dynamic nodes, and scripting.
- Semantic Artifact selection or automatic role disambiguation.
- Routine integration for Projects; this remains Phase 5.

## 3. Architectural Choice

Relay uses a hybrid persistence model:

- Project definitions are stored as canonical JSON on a versioned `projects` row.
- Project Run and step state are stored in normalized tables for atomic claims, status queries, retry history, and restart recovery.
- Every Project Run stores an immutable snapshot of the Project definition and each referenced Task definition/version.
- Each Project node executes as an ordinary Task Run. Existing Attempt, Worker fallback, Artifact, snapshot, lineage, validation, and delivery behavior remains authoritative.

The Project runtime is a daemon-owned persistent state machine. It never holds a database transaction while a Worker process runs.

## 4. Project Definition

Canonical `definition_json`:

```json
{
  "nodes": [
    {"node_id": "collect", "task_id": "TASK-A"},
    {"node_id": "analyze", "task_id": "TASK-B"}
  ],
  "connections": [
    {
      "from_node": "collect",
      "from_role": "raw_data",
      "to_node": "analyze",
      "to_alias": "A1"
    }
  ],
  "output_selection": [
    {"node_id": "analyze", "role": "final_report"}
  ],
  "failure_policy": "stop"
}
```

Project definitions reference Tasks, never historical Task Run IDs. A Project Run creates new Task Runs for its nodes.

### 4.1 Definition validation

Project create/update rejects definitions when:

- `node_id` is empty or duplicated.
- A referenced Task does not currently exist.
- A connection references an unknown node.
- A connection is a self-loop.
- The graph contains a cycle.
- `from_role` is empty.
- `to_alias` is not `A1`, `A2`, and so on.
- Two inputs target the same `(to_node, to_alias)`.
- `output_selection` references an unknown node or empty role.
- `failure_policy` is not `stop`.

Topological validation uses deterministic node ordering so test and receipt output is stable across platforms.

## 5. Artifact Binding

Project connections use explicit role-based bindings:

```text
source node Artifact role -> destination node input alias
```

At runtime, the source Task Run must contain exactly one Artifact whose `role` equals `from_role`:

- Zero matches: `PROJECT_ARTIFACT_MISSING`.
- More than one match: `PROJECT_ARTIFACT_AMBIGUOUS`.
- One match: resolve its immutable Artifact UID, size, and SHA-256, then pass it through the existing Phase 1 snapshot-input mechanism.

Relay does not choose one of multiple matching Artifacts implicitly.

### 5.1 External Project inputs

A Project Run request may bind existing Artifacts to root or other eligible nodes:

```json
{
  "inputs": [
    {
      "node_id": "collect",
      "to_alias": "A1",
      "artifact_uid": "ARTIFACT-X"
    }
  ]
}
```

External inputs are validated and snapshotted at Project Run creation. Their UID, size, SHA-256, destination alias, and snapshot path are recorded in the Project Run snapshot and step input manifest.

An external binding may not collide with a connection targeting the same `(node_id, to_alias)`.

## 6. Database Schema v7

The v6-to-v7 migration is additive and creates four tables.

### 6.1 `projects`

```text
project_id TEXT PRIMARY KEY
name TEXT NOT NULL
description TEXT
version INTEGER NOT NULL DEFAULT 1
definition_json TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
deleted_at TEXT
```

Project updates mutate the definition row and increment `version`. Delete sets `deleted_at`; it does not delete Project Runs, child Task Runs, Artifacts, or lineage.

### 6.2 `project_runs`

```text
project_run_id TEXT PRIMARY KEY
project_id TEXT NOT NULL
project_version INTEGER NOT NULL
project_snapshot_json TEXT NOT NULL
status TEXT NOT NULL
trigger_type TEXT NOT NULL
submitted_via TEXT NOT NULL
final_artifact_ids_json TEXT
warnings_json TEXT
receipt_json TEXT
created_at TEXT NOT NULL
started_at TEXT
completed_at TEXT
updated_at TEXT NOT NULL
```

Project Run statuses for Phase 4:

```text
accepted | running | completed | failed | cancelled
```

### 6.3 `project_run_steps`

```text
project_run_id TEXT NOT NULL
node_id TEXT NOT NULL
task_id TEXT NOT NULL
task_version INTEGER NOT NULL
status TEXT NOT NULL
active_task_run_id TEXT
input_manifest_json TEXT
resolved_connections_json TEXT
error_code TEXT
error_message TEXT
started_at TEXT
completed_at TEXT
updated_at TEXT NOT NULL
PRIMARY KEY (project_run_id, node_id)
```

Step statuses:

```text
pending | ready | queued | running | completed | failed | blocked | cancelled
```

### 6.4 `project_step_runs`

```text
project_run_id TEXT NOT NULL
node_id TEXT NOT NULL
step_attempt INTEGER NOT NULL
task_run_id TEXT NOT NULL
worker_override TEXT
status TEXT NOT NULL
created_at TEXT NOT NULL
completed_at TEXT
PRIMARY KEY (project_run_id, node_id, step_attempt)
UNIQUE (task_run_id)
```

Every Project-level step rerun creates a new ordinary Task Run and a new `project_step_runs` row. Failed Task Runs are never overwritten.

## 7. Immutable Project Run Snapshot

Project Run creation resolves and stores:

- Project ID, version, and complete definition.
- Every node's Task ID, Task version, and complete Task definition.
- External input Artifact UID, size, SHA-256, alias, and snapshot metadata.
- Output selection and failure policy.
- Caller/trigger/submission metadata.

The runtime dispatches nodes from this snapshot, not from current mutable Task rows. A Task or Project edit/delete after Project Run creation cannot alter that run.

The engine therefore gains a snapshot execution entry point that creates an ordinary Task Run from a resolved Task definition without reloading the current Task row.

## 8. Persistent Runtime

The daemon owns a `ProjectRuntime` maintenance loop. Each tick:

1. Atomically claims eligible Project Runs.
2. Reconciles queued/running steps with their child Task Run status.
3. Marks completed or failed steps.
4. Resolves Artifact connections from newly completed steps.
5. Marks nodes `ready` only when all upstream dependencies are completed and all bindings resolve.
6. Atomically claims and queues every ready node.
7. Finalizes the Project Run when all steps complete or failure/cancellation requires termination.

Independent ready nodes are queued in the same tick, enabling existing daemon Job executors to run them in parallel. Fan-in nodes wait for every upstream dependency.

Claim transitions use short SQLite transactions and conditional status updates. File copies and Worker execution happen outside write transactions.

### 8.1 Restart recovery

After daemon restart:

- `queued`/`running` steps with an `active_task_run_id` are reconciled, not requeued.
- Completed child Task Runs are incorporated and their Artifact bindings resolved.
- Ready steps without an active Task Run are claimed once and queued.
- Duplicate dispatch is prevented by step status conditions plus unique `project_step_runs` keys.
- Terminal Project Runs are not reopened automatically.

## 9. Failure and Rerun Semantics

Existing Task Run Attempt retry and Worker fallback handle technical Worker failures inside a node.

If a node reaches terminal failure:

- The step becomes `failed`.
- Descendants remain `blocked`.
- The Project Run becomes `failed` under the Phase 4 `stop` policy.
- Completed upstream steps and their Artifact snapshots remain available.

Supported remediation:

### 9.1 Retry failed node

Create a new Task Run for the failed node using the same recorded input snapshots. On success, unblock and continue descendants.

### 9.2 Retry from a selected node

Reset the selected node and all descendants to `pending`, preserve successful upstream steps and their Artifact snapshots, and create new Task Runs as nodes become ready. Previously executed downstream Task Runs remain in history.

An optional Worker override applies only to the first rerun node. It is recorded in `project_step_runs`.

### 9.3 Full rerun

Calling Project run again creates a new Project Run and resolves the current Project/Task definitions into a new immutable snapshot.

## 10. Final Artifacts and Receipt

At completion, each `(node_id, role)` in `output_selection` must resolve to exactly one Artifact. Missing or ambiguous final selection fails finalization with the same strict role rules.

The Project Run receipt contains:

- Project ID/version and Project Run ID.
- Overall status and timing.
- Node-by-node Task ID/version and final Task Run ID.
- Every Task Run created by Project-level reruns.
- Resolved Artifact connections and external input bindings.
- Final Artifact UIDs.
- Fallback warnings, failed node, and structured error.
- Retry/resume history.

## 11. API

Authenticated daemon endpoints:

```text
POST   /v1/projects
GET    /v1/projects
GET    /v1/projects/{project_id}
POST   /v1/projects/{project_id}
DELETE /v1/projects/{project_id}

POST   /v1/projects/{project_id}/run
GET    /v1/projects/{project_id}/runs

GET    /v1/project-runs/{project_run_id}
GET    /v1/project-runs/{project_run_id}/steps
GET    /v1/project-runs/{project_run_id}/receipt
POST   /v1/project-runs/{project_run_id}/retry
POST   /v1/project-runs/{project_run_id}/cancel
```

Retry payload:

```json
{
  "from_node": "analyze",
  "worker": "codex"
}
```

Omitting `from_node` retries the failed node. A full rerun uses `POST /v1/projects/{project_id}/run` and creates a new Project Run.

Stable Phase 4 errors include:

```text
PROJECT_NOT_FOUND
PROJECT_RUN_NOT_FOUND
PROJECT_INVALID
PROJECT_CYCLE
PROJECT_TASK_MISSING
PROJECT_ARTIFACT_MISSING
PROJECT_ARTIFACT_AMBIGUOUS
PROJECT_INPUT_CONFLICT
PROJECT_RETRY_INVALID
PROJECT_RUN_TERMINAL
```

## 12. CLI

```text
relay project create --file project.json
relay project list
relay project show <project-id>
relay project update <project-id> --file project.json
relay project delete <project-id>
relay project run <project-id> --input collect:A1=ARTIFACT_UID
relay project runs <project-id>

relay project-run show <project-run-id>
relay project-run steps <project-run-id>
relay project-run receipt <project-run-id>
relay project-run retry <project-run-id> [--from-node analyze] [--worker codex]
relay project-run cancel <project-run-id>
```

All commands support `--machine` and stable JSON. Project definition files are UTF-8 JSON and are validated by the same domain validator as API payloads.

## 13. Component Boundaries

```text
relay/projects/models.py
  ProjectSpec, ProjectNode, ProjectConnection, validation, canonical snapshots

relay/projects/service.py
  Project CRUD, Project Run creation, external input snapshot creation,
  retry/cancel operations, receipt assembly

relay/projects/runtime.py
  Persistent tick/reconciliation/readiness/dispatch/finalization

relay/db.py
  Schema v7 and atomic Project DB primitives only

relay/engine.py
  Ordinary Task Run creation from an immutable Task-definition snapshot

relay/api.py / relay/daemon.py / relay/cli.py
  Thin transport and presentation layers
```

## 14. Testing and Acceptance

Testing is divided into:

1. v6-to-v7 migration, idempotent reopen, row preservation, and atomic claims.
2. DAG validation, deterministic topological readiness, input conflicts, and role uniqueness.
3. Sequential, fan-out parallel, fan-in join, restart recovery, cancellation, failure, failed-node retry, and from-node retry.
4. API/CLI schemas and a complete daemon integration flow.

Acceptance flow:

```text
Collect Task
  raw_data    -> Analyze Task.A1
  source_list -> Final Task.A1

Analyze Task
  analysis    -> Final Task.A2

Chart Task
  chart       -> Final Task.A3
```

Collect runs first. Analyze and Chart then run in parallel. Final waits for all required inputs and runs after the join. The test verifies child Task Runs, exact Artifact UID/role/alias/hash snapshots, lineage, final Artifact selection, Project receipt, restart safety, and absence of duplicate dispatch.

## 15. Completion Criteria

Phase 4 is complete when:

- Sequential, simple parallel, and join Projects execute correctly.
- Every Task node produces an ordinary Task Run linked to its Project Run step.
- Every connection records exactly which Artifact UID entered which alias.
- Failed nodes can be retried with the same input snapshots; retry-from-node preserves upstream outputs.
- Project and Task edits do not mutate an in-progress or historical Project Run snapshot.
- Project deletion preserves Project Runs, child Task Runs, Artifacts, and lineage.
- Daemon restart does not duplicate or lose Project step execution.
- Final Artifact selection and Project Run receipt are complete and deterministic.
- Full tests, Ruff, compileall, release build, and `git diff --check` pass on the implementation branch.
