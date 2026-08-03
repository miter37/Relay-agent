# Phase 4 Project MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent Project layer that connects registered Tasks through explicit Artifact bindings, runs independent nodes in parallel, joins dependent results, recovers safely from daemon restart, and supports failed-node / from-node reruns.

**Architecture:** A schema v7 migration adds `projects`, `project_runs`, `project_run_steps`, and `project_step_runs`. The engine gains `run_task_from_snapshot` that creates ordinary Task Runs from an immutable Task-definition snapshot. A new `relay.projects.service` covers Project CRUD, Project Run creation, retry, and cancel. A `relay.projects.runtime` provides a persistent tick-based state machine wired into the daemon maintenance loop. The Project run uses existing Task Run Attempt/fallback/snapshot/lineage pipelines unchanged.

**Tech Stack:** Python 3.11+, SQLite, standard library, `unittest`, existing loopback daemon/RPC, Ruff.

## Global Constraints

- Phase 4 covers daemon/API/CLI core only; Project Flow GUI is deferred to a later phase (matching the G3 / Task / Schedule precedent).
- DB `CURRENT_SCHEMA_VERSION` bumps 6 -> 7; the migration is additive (four new tables) and touches no existing column or row.
- Project connections use explicit role binding: `{from_node, from_role, to_node, to_alias}`. The source Task Run must contain exactly one Artifact with role `from_role` (zero -> `PROJECT_ARTIFACT_MISSING`, many -> `PROJECT_ARTIFACT_AMBIGUOUS`).
- External Project inputs (binding existing Artifact UIDs to root/eligible node aliases) are validated and snapshotted at Project Run creation.
- `failure_policy` is `stop` only in MVP.
- Each Project Run snapshot freezes Project definition, Task definitions/versions, external inputs, output selection, and failure policy. Edits to Project or Task after Project Run creation never alter that run.
- Project deletion is soft-delete (`deleted_at`) and never touches existing Project Runs, child Task Runs, Artifacts, or lineage (Schedule-deletion precedent + Project Rule).
- Every Project-level rerun produces a new ordinary Task Run; failed Task Runs are never overwritten.
- `relay-receipt.json`, `test_result.json`, `test_task.md` are never staged.
- Version string stays `1.1.0` (Phase 4 is additive; no release cut in this plan).
- Work continues on the current `feat/phase0-domain-compat` branch (Phase 0/1/2/3 live here as sequential commits).

---

### Task 1: Schema v7 migration and Project DB primitives

**Files:**
- Modify: `relay/db.py` (SCHEMA constant, `CURRENT_SCHEMA_VERSION`, migration constants, `migrate()` dispatch, CRUD methods)
- Modify: `tests/test_migrations.py`
- Create: `tests/test_phase4_db.py`

**Interfaces:**
- Produces: `Database.create_project(row)`, `Database.get_project(project_id)`, `Database.list_projects(*, name=None, limit=50)`, `Database.update_project(project_id, **changes)`, `Database.soft_delete_project(project_id)`, `Database.create_project_run(row)`, `Database.get_project_run(project_run_id)`, `Database.list_project_runs(*, project_id, limit=50)`, `Database.create_or_update_project_step(row)`, `Database.get_project_step(project_run_id, node_id)`, `Database.list_project_steps(project_run_id)`, `Database.append_project_step_run(project_run_id, node_id, task_run_id, worker_override)`, `Database.list_project_step_runs(project_run_id, node_id)`, `Database.claim_ready_steps(project_run_id, runnable_status, claimed_status, now) -> list of (project_run_id, node_id)` (atomic).
- Produces: `Database.migrate()` handles version 6 -> 7 idempotently.

- [ ] **Step 1: Write the failing DB tests**

Create `tests/test_phase4_db.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database
from relay.errors import RelayError


class Phase4DBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "relay.db"
        self.db = Database(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_migration_6_to_7_creates_project_tables(self):
        with __import__("sqlite3").connect(self.path) as conn, conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, CURRENT_SCHEMA_VERSION)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"projects", "project_runs", "project_run_steps", "project_step_runs"} <= tables)

    def test_project_crud_and_soft_delete(self):
        self.db.create_project({
            "project_id": "p-1", "name": "Weekly", "description": None,
            "version": 1, "definition_json": "{}",
        })
        self.assertEqual(self.db.get_project("p-1")["name"], "Weekly")
        listed = self.db.list_projects()
        self.assertEqual([p["project_id"] for p in listed], ["p-1"])
        self.db.update_project("p-1", name="Renamed", version=2)
        self.assertEqual(self.db.get_project("p-1")["name"], "Renamed")
        self.assertTrue(self.db.soft_delete_project("p-1"))
        self.assertIsNotNone(self.db.get_project("p-1")["deleted_at"])
        self.assertEqual(self.db.list_projects(), [])

    def test_project_run_and_step_round_trip(self):
        self.db.create_project({
            "project_id": "p-1", "name": "P", "description": None,
            "version": 1, "definition_json": "{\"nodes\":[]}",
        })
        self.db.create_project_run({
            "project_run_id": "pr-1", "project_id": "p-1", "project_version": 1,
            "project_snapshot_json": "{}", "status": "accepted",
            "trigger_type": "manual", "submitted_via": "cli",
        })
        self.db.create_or_update_project_step({
            "project_run_id": "pr-1", "node_id": "n1",
            "task_id": "T-1", "task_version": 1, "status": "ready",
        })
        step = self.db.get_project_step("pr-1", "n1")
        self.assertEqual(step["status"], "ready")
        steps = self.db.list_project_steps("pr-1")
        self.assertEqual([s["node_id"] for s in steps], ["n1"])

    def test_append_project_step_run_is_unique_on_task_run(self):
        self.db.create_project({
            "project_id": "p-1", "name": "P", "description": None,
            "version": 1, "definition_json": "{}",
        })
        self.db.create_project_run({
            "project_run_id": "pr-1", "project_id": "p-1", "project_version": 1,
            "project_snapshot_json": "{}", "status": "running",
            "trigger_type": "manual", "submitted_via": "cli",
        })
        self.db.create_or_update_project_step({
            "project_run_id": "pr-1", "node_id": "n1",
            "task_id": "T-1", "task_version": 1, "status": "running",
            "active_task_run_id": "job-1",
        })
        self.db.append_project_step_run("pr-1", "n1", "job-1", None)
        with self.assertRaises(RelayError):
            self.db.append_project_step_run("pr-1", "n1", "job-2", None)

    def test_claim_ready_steps_is_atomic(self):
        from relay.db import CURRENT_SCHEMA_VERSION
        self.db.create_project({
            "project_id": "p-1", "name": "P", "description": None,
            "version": 1, "definition_json": "{}",
        })
        self.db.create_project_run({
            "project_run_id": "pr-1", "project_id": "p-1", "project_version": 1,
            "project_snapshot_json": "{}", "status": "running",
            "trigger_type": "manual", "submitted_via": "cli",
        })
        for node_id, status in [("a", "ready"), ("b", "ready"), ("c", "queued")]:
            self.db.create_or_update_project_step({
                "project_run_id": "pr-1", "node_id": node_id,
                "task_id": f"T-{node_id}", "task_version": 1, "status": status,
            })
        claimed = self.db.claim_ready_steps("pr-1", "ready", "queued")
        self.assertEqual({n for _, n in claimed}, {"a", "b"})
        step_c = self.db.get_project_step("pr-1", "c")
        self.assertEqual(step_c["status"], "queued")
```

- [ ] **Step 2: Add migration coverage in test_migrations.py**

Append a test that builds a v6 db, sets `PRAGMA user_version=6`, reopens, and asserts all four new tables exist and version is 7.

- [ ] **Step 3: Verify tests fail**

Run: `python -m unittest tests.test_phase4_db tests.test_migrations -v`
Expected: FAIL (no `projects` table).

- [ ] **Step 4: Schema v7 in db.py**

Set `CURRENT_SCHEMA_VERSION = 7`. Append to `SCHEMA` (after `tasks`) and define `MIGRATION_6_TO_7`. Add `projects`, `project_runs`, `project_run_steps`, `project_step_runs` tables with the schema described in the Phase 4 design spec section 6 and appropriate indices. Update `migrate()` to include the `version == 6` branch and add `MIGRATION_6_TO_7` to the `version == 0` chain and the `version in {1..6}` chain.

- [ ] **Step 5: Add CRUD + claim methods**

Append to `Database` (follow `create_task`/`get_task` patterns):
- `create_project(row)`, `get_project`, `list_projects(*, name=None, limit=50)`, `update_project(project_id, **changes)` (bump version + updated_at, validate non-deleted), `soft_delete_project` (set deleted_at, raise `PROJECT_NOT_FOUND` if missing; returns True/False on transition).
- `create_project_run`, `get_project_run`, `update_project_run`, `list_project_runs(*, project_id, limit=50)`.
- `create_or_update_project_step` (UPSERT by `(project_run_id, node_id)`), `get_project_step`, `list_project_steps`.
- `append_project_step_run(project_run_id, node_id, task_run_id, worker_override)` (raises `STEP_RUN_DUPLICATE` if `task_run_id` already used).
- `claim_ready_steps(project_run_id, runnable, claimed, now)` implemented as a single SQLite `UPDATE ... WHERE status=? RETURNING project_run_id,node_id` or an equivalent transactional read+update. Must be atomic (one transaction; `BEGIN IMMEDIATE`).
- Add error codes `PROJECT_NOT_FOUND`, `PROJECT_RUN_NOT_FOUND`, `STEP_RUN_DUPLICATE`, `PROJECT_INPUT_CONFLICT`, `PROJECT_RETRY_INVALID`, `PROJECT_RUN_TERMINAL` to the relevant RelayError throwing sites.

- [ ] **Step 6: Run focused tests and commit**

Run: `python -m unittest tests.test_phase4_db tests.test_migrations -v`
Commit: `git commit -m "feat: add project tables and database primitives (phase 4)"`.

### Task 2: ProjectSpec model and DAG validation

**Files:**
- Create: `relay/projects/__init__.py`, `relay/projects/models.py`
- Create: `tests/test_phase4_models.py`

**Interfaces:**
- `ProjectNode`, `ProjectConnection`, `ProjectOutputSelection`, `ProjectSpec` dataclasses.
- `ProjectSpec.validate(task_lookup: Callable[[str], dict|None])` enforcing definition section 4.1 of the design spec (cycle detection, self-loop, alias uniqueness, A1/A2 naming, duplicate `(to_node,to_alias)`, output_selection validity, `failure_policy` is `stop`, Task existence).
- `ProjectSpec.to_snapshot()` returning canonical JSON string (sort_keys, separators etc.) from `canonical_json`.
- `ProjectSpec.topological_order()` returning a deterministic list of `node_id`s using sorted-name tie-break so test order is stable.
- Consumes: `relay.util.canonical_json`.

- [ ] **Step 1: Write failing tests**

```python
def test_valid_project_passes_validation(sample_connected_def):
    spec = ProjectSpec.from_dict(sample_connected_def)
    spec.validate({tid: True for tid in ("T-A", "T-B", "T-C", "T-D")})

def test_cycle_rejected():
    spec = ProjectSpec(nodes=[ProjectNode("a","T-A"), ProjectNode("b","T-B")],
                       connections=[ProjectConnection("a","raw","b","A1"),
                                    ProjectConnection("b","raw","a","A1")],
                       output_selection=ProjectSpec.OutputSelection([]),
                       failure_policy="stop")
    with self.assertRaisesRegex(RelayError, "PROJECT_CYCLE"):
        spec.validate({"T-A": True, "T-B": True})

def test_duplicate_alias_rejected():
    spec = ProjectSpec(nodes=[ProjectNode("a","T-A"), ProjectNode("b","T-B")],
                       connections=[ProjectConnection("a","raw","b","A1"),
                                    ProjectConnection("a","other","b","A1")],
                       output_selection=ProjectSpec.OutputSelection([]),
                       failure_policy="stop")
    with self.assertRaisesRegex(RelayError, "INPUT_CONFLICT|duplicate|inputs target"):
        spec.validate({"T-A": True, "T-B": True})

def test_missing_task_rejected():
    spec = ProjectSpec(nodes=[ProjectNode("a","T-MISSING")],
                       connections=[],
                       output_selection=ProjectSpec.OutputSelection([]),
                       failure_policy="stop")
    with self.assertRaisesRegex(RelayError, "PROJECT_TASK_MISSING"):
        spec.validate({})
```

- [ ] **Step 2: Implement models**

- `relay/projects/__init__.py` empty.
- `relay/projects/models.py` implementing the dataclasses, `from_dict`, `to_snapshot`, `validate` (load tasks by `task_lookup`; for cycle detection run DFS with recursion stack; deterministic topo order via Kahn's algorithm with sorted-name tie-break), `ProjectSpec.OutputSelection` named tuple inside the dataclass module.
- Use stable error codes matching the design spec.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase4_models -v`
Commit: `git commit -m "feat: add ProjectSpec model with DAG validation (phase 4)"`.

### Task 3: Engine snapshot-based Task Run creation + retry-able queue path

**Files:**
- Modify: `relay/engine.py` (add `run_task_from_snapshot`, expose `register_project_runtime`, add `load_task(task_id)` and `task_snapshot_for_project`)
- Create: `tests/test_phase4_engine.py`

**Interfaces:**
- `RelayEngine.run_task_from_snapshot(task_snapshot: dict, *, queued: bool, submitted_via: str, caller: str) -> tuple[dict, bool]` reusing `create_job(task_definition_snapshot,...)`.
- `RelayEngine.load_task_for_snapshot(task_id) -> dict` returning `{task_id, name, version, definition}` for frozen Project snapshot use.
- `RelayEngine.record_step_dispatch(project_run_id, node_id, task_run_id)` updates `active_task_run_id` only if not already set; mirrors Task Run completion into project step.

- [ ] **Step 1: Write failing tests**

```python
class Phase4EngineTests(unittest.TestCase):
    def test_run_task_from_snapshot_carries_project_run_origin(self):
        # Build task in DB, snapshot it, run from snapshot.
        # Expect returned Job carries task_id and task_version from snapshot, not from current row.
        ...
    def test_load_task_for_snapshot_returns_required_fields(self):
        # Create task with description, instructions, etc.; call load_task_for_snapshot; assert keys.
        ...
```

- [ ] **Step 2: Implement**

In `relay/engine.py`, add the helpers. `run_task_from_snapshot` builds a `JobRequest` from the snapshot dict and calls `create_job` with `task_id=task_snapshot["task_id"]`, `task_definition={"task_id", "name", "version", "instructions", policy fields, schema/contract/policy blobs}`. This reuses Phase 3 work without touching the current snapshot logic.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase4_engine -v`
Commit: `git commit -m "feat: support snapshot-based Task Run execution (phase 4)"`.

### Task 4: Project service (CRUD, Project Run creation, external inputs, retry, cancel)

**Files:**
- Create: `relay/projects/service.py`
- Create: `tests/test_phase4_service.py`

**Interfaces:**
- `ProjectService(db, engine)` constructor.
- `create_project(spec_dict, task_lookup) -> dict` validates, stores, returns project row.
- `update_project(project_id, spec_dict, task_lookup) -> dict` mutates row, bumps version, validates.
- `soft_delete_project(project_id) -> bool`.
- `get_project(project_id)`, `list_projects(name=None, limit=50)`.
- `create_project_run(project_id, *, trigger_type, submitted_via, caller, inputs=None) -> dict`:
   - Loads Project, loads every referenced Task, snapshots Tasks into the project_run `project_snapshot_json`.
   - Validates external input UIDs exist, locks each Artifact's size/shape, copies each to `input_snapshot_root/<node>_<alias>` with SHA-256 verify, records `{node_id, to_alias, artifact_uid, source_job_id, source_relative_path, source_sha256, source_size, snapshot_relative_path, snapshot_sha256, snapshot_size, binding_mode="snapshot"}` per input.
   - Resolves connections from the snapshot definitions into a per-node `input_manifest_json`.
   - Creates `project_runs` row with `status=accepted`, and one `project_run_steps` per node with `status=pending` for non-root nodes and `status=ready` for roots that have all external inputs satisfied (else `pending` if external inputs are still pending).
   - Returns `{project_run, steps}` payload.
- `retry_project_run(project_run_id, *, from_node=None, worker=None) -> dict` either reruns only the failed step (default) or resets `from_node` and all descendants to `pending`. Preserves successful upstream step rows and their snapshot rows.
- `cancel_project_run(project_run_id) -> dict` cancels only if not terminal.
- `project_run_receipt(project_run_id) -> dict` assembles the structured receipt.

- [ ] **Step 1: Write failing tests**

Cover:
- `create_project_run` validates graph, rejects duplicate alias connections (`PROJECT_INPUT_CONFLICT`).
- `create_project_run` snapshots Tasks and external inputs (write a real file under `input_snapshot_root`).
- `retry_project_run` without `from_node` creates one new `project_step_runs` row whose `task_run_id` differs from the failed step (and old `task_run` row is preserved).
- `retry_project_run` with `from_node` resets downstream steps to `pending`.
- `cancel_project_run` on `completed` raises `PROJECT_RUN_TERMINAL`.
- `project_run_receipt` returns shape with `final_artifact_ids`, `warnings`, `nodes`.

- [ ] **Step 2: Implement service**

Implement the service as a thin layer over the engine and DB. Use `canonical_json` for all storage. Project Run `project_snapshot_json` structure: `{"project_id", "project_version", "project_definition", "task_snapshots": {task_id: {name, version, instructions, default_worker, fallback_enabled, timeout_seconds, profile, result_format, input_schema, output_contract, validation_policy}}, "external_inputs": [...], "failure_policy", "output_selection"}`.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase4_service -v`
Commit: `git commit -m "feat: add Project service for CRUD, run creation, retry, cancel (phase 4)"`.

### Task 5: Project runtime (persistent state machine + daemon maintenance)

**Files:**
- Create: `relay/projects/runtime.py`
- Create: `tests/test_phase4_runtime.py`
- Modify: `relay/daemon.py` (wire runtime into maintenance loop and ProjectService initialization)

**Interfaces:**
- `ProjectRuntime(db, engine, service, *, tick_seconds=1.0)`.
- `RuntimeState.done_wake.wait()` (used by tests).
- `RuntimeState.tick_once()` runs one reconciliation cycle:
  1. Reconcile each `running` step with its `active_task_run_id` (read Job row, mark step `completed`/`failed` accordingly).
  2. For each newly completed step, resolve Artifact bindings: find the unique Artifact matching `from_role` from the step's Task Run, validate hash, build `resolved_connections_json` entries, and for each affected descendant node accumulate bindings.
  3. For each step whose stored inputs are now fully resolved and upstream steps are all completed, atomically transition `pending`/`ready` -> `claimed via claim_ready_steps` -> `queued` and call `engine.run_task_from_snapshot` per step.
  4. Mark the Project Run `completed` when all steps are terminal-completed; mark `failed` if any step terminal-failed (under `stop` policy).
- `RuntimeState.start()` launches a daemon thread with `ticks_until_idle`, `stop()` joins it.
- Restart safety: when started, it scans for `running`/`ready` steps and reconciles them before queuing anything.

- [ ] **Step 1: Write failing tests**

```python
class ProjectRuntimeTests(unittest.TestCase):
    def test_sequential_three_step_runs_serially(self):
        # Build Project A->B->C. Assert Task Run ordering and final Project Run status.
        ...
    def test_parallel_fanout_then_fanin(self):
        # Build Project collect -> (analyze, chart) -> final. Use stub Task workers that produce Artifacts.
        # Use runtime + real engine; expect one tick where both analyze and chart are queued, then final queued.
        ...
    def test_role_ambiguity_fails_project_run(self):
        # Source Task produces 2 Artifacts with same role; expect PROJECT_ARTIFACT_AMBIGUOUS.
        ...
    def test_role_missing_fails_project_run(self):
        # Source Task produces 0 Artifacts with expected role; expect PROJECT_ARTIFACT_MISSING.
        ...
    def test_restart_does_not_duplicate_dispatch(self):
        # Dispatch once, capture active_task_run_id; simulate restart by creating a new runtime; assert no second Task Run row.
        ...
    def test_failed_node_retry_uses_same_inputs(self):
        # Run Project with one failing Task; retry without from_node; verify new step attempt row, upstream snapshot preserved.
        ...
```

For determinism in tests, use a `StubWorker` registered as a custom Agent App (mirroring the Phase 3 pattern of using a custom Agent App for tests) that returns a JSON result containing a single Artifact with a known role.

- [ ] **Step 2: Implement runtime**

Build the runtime as above. Use the engine's `run_task_from_snapshot`. Bind it to the daemon in `relay/daemon.py` via `self.maintenance.project_runtime = ProjectRuntime(self.db, self.engine, self.service)` and start/stop it with the existing maintenance loop pattern. Ensure tick work doesn't hold a transaction during long-running steps.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase4_runtime -v`
Commit: `git commit -m "feat: add persistent Project runtime (phase 4)"`.

### Task 6: API functions

**Files:**
- Modify: `relay/api.py`
- Create: `tests/test_phase4_api.py`

**Interfaces:**
- `create_project(engine, payload)`, `list_projects(engine, *, name=None)`, `get_project(engine, project_id)`, `update_project(engine, project_id, payload)`, `delete_project(engine, project_id)`, `run_project(engine, project_id, payload)`, `project_runs(engine, project_id)`.
- `project_run(engine, project_run_id)`, `project_run_steps(engine, project_run_id)`, `project_run_receipt(engine, project_run_id)`, `project_run_retry(engine, project_run_id, payload)`, `project_run_cancel(engine, project_run_id)`.

- [ ] **Step 1: Tests** (similar to Phase 3 API tests but using Project service through engine).

- [ ] **Step 2: Implement** — thin wrappers returning `{"ok": True, ...}` and mapping RelayError to API responses.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase4_api -v`
Commit: `git commit -m "feat: expose project API functions (phase 4)"`.

### Task 7: Daemon routes

**Files:**
- Modify: `relay/daemon.py`
- Create: `tests/test_phase4_daemon.py`

**Routes:**
- `GET/POST /v1/projects`, `GET/POST/DELETE /v1/projects/{id}`, `POST /v1/projects/{id}/run`, `GET /v1/projects/{id}/runs`.
- `GET /v1/project-runs/{id}`, `GET /v1/project-runs/{id}/steps`, `GET /v1/project-runs/{id}/receipt`, `POST /v1/project-runs/{id}/retry`, `POST /v1/project-runs/{id}/cancel`.
- Add `project-runtime` capability to `/health`.

- [ ] **Step 1: Tests** — use the existing daemon boot pattern from `tests/test_phase3_api.py`; assert routes' payloads.

- [ ] **Step 2: Implement** — wire routes to API functions via `self.daemon.engine` and the ProjectService. Update the `/health` capability list to add `"project-runtime"`.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase4_daemon -v`
Commit: `git commit -m "feat: route project daemon endpoints (phase 4)"`.

### Task 8: CLI commands

**Files:**
- Modify: `relay/cli.py`
- Create: `tests/test_phase4_cli.py`

**Commands:**
- `relay project {create|list|show|update|delete|run|runs}`.
- `relay project-run {show|steps|receipt|retry|cancel}`.

Follow `_add_task_parsers` pattern. Add `"project", "project-run"` to `COMMANDS` and extend `_preprocess` for any compound syntax. All commands accept `--machine`.

- [ ] **Step 1: Tests** — parse each command path and assert namespace fields.

- [ ] **Step 2: Implement** — add `_add_project_parsers`, `_add_project_run_parsers`, and `_project_cli_request`. Dispatch from `main()` via `elif args.command == "project": ...` and `elif args.command == "project-run": ...`.

- [ ] **Step 3: Run and commit**

Run: `python -m unittest tests.test_phase4_cli -v`
Commit: `git commit -m "feat: add project CLI commands (phase 4)"`.

### Task 9: Integration coverage + restart safety + acceptance flow

**Files:**
- Create: `tests/test_phase4_integration.py`

- [ ] **Step 1: Acceptance test**

Implement the canonical acceptance flow described in design spec section 14:

```text
Collect Task    raw_data     -> Analyze Task A1
Collect Task    source_list  -> Final Task A1
Analyze Task    analysis     -> Final Task A2
Chart Task      chart        -> Final Task A3
```

Use stub workers returning deterministic JSON results that declare `role: raw_data` / `role: source_list` / `role: analysis` / `role: chart`. The acceptance test must verify:

- Two parallel Task Runs for `analyze` and `chart` start in the same runtime tick.
- Final Project Run reaches `completed`.
- Every node's `active_task_run_id` maps to a real Task Run with a `task_snapshot_json` whose `task_definition` matches the snapshot taken at Project Run creation.
- Project Run receipt contains final Artifact UIDs from Final Task's outputs A1/A2/A3.
- `resolved_connections_json` references the exact Artifact UID, role, alias, source Task Run, and snapshot SHA-256.

- [ ] **Step 2: Restart safety test**

- Run the acceptance flow to completion.
- Recreate a `ProjectRuntime` over the same DB.
- Assert no duplicate Task Runs appear.

- [ ] **Step 3: Run + commit**

Run: `python -m unittest tests.test_phase4_integration -v`
Commit: `git commit -m "test: phase 4 acceptance and restart coverage"`.

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
Expected: all green; Phase 0/1/2/3 and all G-series suites pass.

- [ ] **Step 2: Confirm scope and user files**

Run `git status --short --branch` and `git ls-files --others --exclude-standard`.
Expected: `relay-receipt.json`, `test_result.json`, `test_task.md` remain untracked; no unintended files staged.

- [ ] **Step 3: Final commit if applicable**

Commit: `git commit -m "test: phase 4 final verification"` if anything extra was added.

## Stop Gate

- Sequential, fan-out parallel, and fan-in Projects all execute correctly.
- Every Task node produces an ordinary Task Run linked to its Project Run step.
- Every connection records exactly which Artifact UID entered which alias with SHA-256 snapshot.
- Failed nodes can be retried with the same input snapshots; retry-from-node preserves upstream outputs and resets downstream pending.
- Project and Task edits and deletes never alter an in-progress or historical Project Run snapshot.
- Daemon restart does not duplicate or lose Project step execution.
- Final Artifact selection and Project Run receipt are complete and deterministic.
- CLI, daemon API, runtime, service, and DB primitives each have focused tests; the full suite, Ruff, and compileall pass.
- The `/health` capabilities list advertises `project-runtime` without breaking the GUI compatibility floor.
- `relay-receipt.json`, `test_result.json`, and `test_task.md` are never staged.
