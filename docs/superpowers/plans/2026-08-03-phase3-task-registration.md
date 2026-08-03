# Phase 3 Task Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class reusable Task definition layer to Relay so the same Task can be run repeatedly while each Run preserves the exact Task definition and settings that produced it.

**Architecture:** A new `tasks` table is added via an additive schema migration. The engine gains Task CRUD and a `run_task` path that builds a standard `JobRequest` from the Task's policy defaults, stamps the resulting Job's `task_id`, and copies the full Task definition into the existing `task_snapshot_json`. A `save_run_as_task` promotion derives a Task from any Run's snapshot. All flows reuse the Phase 0/1 Run, Artifact, and lineage machinery unchanged.

**Tech Stack:** Python 3.11+, SQLite, standard library, `unittest`, existing loopback daemon/RPC, Ruff.

## Global Constraints

- Phase 3 covers CLI + daemon/API core only; Task management GUI is deferred to a later phase (G3 Schedule precedent).
- DB `CURRENT_SCHEMA_VERSION` bumps 5 -> 6; the migration is additive (`CREATE TABLE IF NOT EXISTS tasks`) and touches no existing column or row.
- Legacy ad-hoc Jobs stay `task_id IS NULL`; no backfill, no batch migration.
- `input_schema`, `output_contract`, and `validation_policy` are stored and surfaced only; enforcement is Phase 6.
- Deleting a Task removes only the definition row; all Runs, Artifacts, snapshots, and lineage survive (Schedule deletion precedent + Project Rule).
- `relay-receipt.json`, `test_result.json`, `test_task.md` are never staged.
- Every feature follows failing test -> RED -> minimal implementation -> GREEN -> commit.
- Version string stays `1.1.0` (Phase 3 is additive; no release cut in this plan).
- Work continues on the current `feat/phase0-domain-compat` branch (Phase 0/1/2 live here as sequential commits).

---

### Task 1: Schema migration and Task DB primitives

**Files:**
- Modify: `relay/db.py` (SCHEMA constant, `CURRENT_SCHEMA_VERSION`, migration constant, `migrate()` dispatch, CRUD methods)
- Modify: `tests/test_migrations.py`
- Create: `tests/test_phase3_db.py`

**Interfaces:**
- Produces: `Database.create_task(row)`, `Database.get_task(task_id)`, `Database.list_tasks(*, name=None, limit=50)`, `Database.update_task(task_id, **changes)`, `Database.delete_task(task_id) -> bool`, `Database.runs_for_task(task_id, *, limit=50)`.
- Produces: `Database.migrate()` handles version 5 -> 6 idempotently.

- [ ] **Step 1: Write the failing DB tests**

Create `tests/test_phase3_db.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.db import Database


class TaskDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "relay.db")

    def tearDown(self):
        self.temp.cleanup()

    def _row(self, **overrides):
        base = {
            "task_id": "task-1",
            "name": "Weekly report",
            "description": None,
            "instructions": "Write a weekly report",
            "default_worker": "auto",
            "fallback_enabled": 1,
            "timeout_seconds": None,
            "profile": "web-research",
            "result_format": "json",
            "input_schema": None,
            "output_contract": None,
            "validation_policy": None,
            "version": 1,
        }
        base.update(overrides)
        return base

    def test_create_and_get_task(self):
        self.db.create_task(self._row())
        task = self.db.get_task("task-1")
        self.assertEqual(task["name"], "Weekly report")
        self.assertEqual(task["version"], 1)

    def test_get_missing_task_returns_none(self):
        self.assertIsNone(self.db.get_task("nope"))

    def test_list_tasks_filters_by_name_and_orders_newest(self):
        self.db.create_task(self._row(task_id="task-1", name="Alpha"))
        self.db.create_task(self._row(task_id="task-2", name="Beta report"))
        names = [t["name"] for t in self.db.list_tasks()]
        self.assertEqual(names, ["Beta report", "Alpha"])
        filtered = self.db.list_tasks(name="report")
        self.assertEqual([t["task_id"] for t in filtered], ["task-2"])

    def test_update_task_bumps_version_and_merges_fields(self):
        self.db.create_task(self._row())
        self.db.update_task("task-1", name="Renamed", instructions="New instructions")
        task = self.db.get_task("task-1")
        self.assertEqual(task["name"], "Renamed")
        self.assertEqual(task["instructions"], "New instructions")
        self.assertEqual(task["version"], 2)

    def test_delete_task_returns_true_and_removes_row(self):
        self.db.create_task(self._row())
        self.assertTrue(self.db.delete_task("task-1"))
        self.assertIsNone(self.db.get_task("task-1"))
        self.assertFalse(self.db.delete_task("task-1"))

    def test_runs_for_task_lists_linked_jobs(self):
        self.db.create_task(self._row())
        for jid, tid in [("job-a", "task-1"), ("job-b", "task-1"), ("job-c", None)]:
            self.db.create_job({
                "job_id": jid, "caller": "human", "submitted_via": "cli",
                "task_hash": "h", "requested_worker": "auto", "format": "json",
                "profile": "web-research", "output_path": "o", "artifact_path": "a",
                "status": "QUEUED", "request_json": "{}", "task_id": tid,
            })
        runs = self.db.runs_for_task("task-1")
        self.assertEqual([r["job_id"] for r in runs], ["job-b", "job-a"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add migration coverage to test_migrations.py**

Append a test that builds a fresh v5 db in a temp dir, sets `PRAGMA user_version=5`, reopens it, and asserts the `tasks` table exists and the version is 6. Do not touch checked-in fixtures.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m unittest tests.test_phase3_db tests.test_migrations -v`
Expected: FAIL (no `tasks` table, no `create_task`).

- [ ] **Step 4: Add the tasks table and migration**

In `relay/db.py`, set `CURRENT_SCHEMA_VERSION = 6`. Append to the `SCHEMA` string (after the `capability_audits` block):

```sql
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    instructions TEXT,
    default_worker TEXT,
    fallback_enabled INTEGER NOT NULL DEFAULT 1,
    timeout_seconds INTEGER,
    profile TEXT,
    result_format TEXT,
    input_schema TEXT,
    output_contract TEXT,
    validation_policy TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_task ON jobs(task_id, created_at);
```

Add a `MIGRATION_5_TO_6` constant with the identical DDL. In `migrate()`, add it to both the `version == 0` chain (after the `MIGRATION_4_TO_5` loop) and the `version in {1,2,3,4}` chain, and add a new `elif version == 5:` branch that runs only `MIGRATION_5_TO_6` inside a `BEGIN`/`COMMIT` with backup (mirror the existing `elif version in {1, 2, 3, 4}:` block).

- [ ] **Step 5: Add the CRUD methods**

Append to the `Database` class in `relay/db.py`, following the `create_schedule`/`get_schedule` patterns:

```python
    def create_task(self, row: dict[str, Any]) -> None:
        now = utc_now()
        values = {"version": 1, **row, "created_at": row.get("created_at", now), "updated_at": now}
        keys = list(values)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO tasks ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                [values[key] for key in keys],
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(self, *, name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        params: list[Any] = []
        if name:
            query += " WHERE name LIKE ?"
            params.append(f"%{name}%")
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def update_task(self, task_id: str, **changes: Any) -> None:
        if not changes:
            return
        task = self.get_task(task_id)
        if not task:
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        changes["version"] = task["version"] + 1
        changes["updated_at"] = utc_now()
        keys = list(changes)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {','.join(f'{key}=?' for key in keys)} WHERE task_id=?",
                [changes[key] for key in keys] + [task_id],
            )

    def delete_task(self, task_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))
            return cur.rowcount > 0

    def runs_for_task(self, task_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
```

- [ ] **Step 6: Run the focused tests and commit**

Run: `python -m unittest tests.test_phase3_db tests.test_migrations -v`
Expected: PASS.
Commit: `git commit -m "feat: add task table and database primitives"`.

---

### Task 2: TaskSpec model

**Files:**
- Modify: `relay/models.py`
- Create: `tests/test_phase3_flows.py` (model section; engine section is added in Task 3)

**Interfaces:**
- Produces: `TaskSpec` dataclass with `validate()`, `to_row()`, `normalize_changes(changes)`, and classmethod `from_snapshot(snapshot, request, *, name, description)`.
- Consumes: `new_job_id`, `utc_now` from `relay/util.py`.

- [ ] **Step 1: Write a failing test**

In `tests/test_phase3_flows.py`, add a `TaskSpecTests` class:

```python
class TaskSpecTests(unittest.TestCase):
    def test_validate_name_and_instructions(self):
        spec = TaskSpec(name="Report", instructions="Write it")
        spec.validate()
        row = spec.to_row()
        self.assertTrue(row["task_id"])
        self.assertEqual(row["version"], 1)

    def test_rejects_missing_name(self):
        with self.assertRaisesRegex(RelayError, "TASK_NAME_REQUIRED"):
            TaskSpec(name="   ", instructions="x").validate()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m unittest tests.test_phase3_flows -v`
Expected: FAIL (no `TaskSpec`).

- [ ] **Step 3: Add TaskSpec to models.py**

```python
@dataclass(slots=True)
class TaskSpec:
    name: str
    instructions: str
    description: str | None = None
    default_worker: str | None = "auto"
    fallback_enabled: bool = True
    timeout_seconds: int | None = None
    profile: str | None = None
    result_format: str | None = None
    input_schema: str | None = None
    output_contract: str | None = None
    validation_policy: str | None = None
    task_id: str | None = None
    version: int = 1

    def validate(self) -> None:
        from .errors import RelayError
        if not str(self.name or "").strip():
            raise RelayError("TASK_NAME_REQUIRED", "A task name is required.")
        if not str(self.instructions or "").strip():
            raise RelayError("TASK_INVALID", "Task instructions are required.")
        if self.result_format and self.result_format not in {"json", "txt"}:
            raise RelayError("TASK_INVALID", "result_format must be json or txt.")

    def to_row(self) -> dict[str, Any]:
        from .util import new_job_id, utc_now
        self.validate()
        now = utc_now()
        return {
            "task_id": self.task_id or new_job_id(),
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "default_worker": self.default_worker,
            "fallback_enabled": 1 if self.fallback_enabled else 0,
            "timeout_seconds": self.timeout_seconds,
            "profile": self.profile,
            "result_format": self.result_format,
            "input_schema": self.input_schema,
            "output_contract": self.output_contract,
            "validation_policy": self.validation_policy,
            "version": self.version,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def normalize_changes(changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name", "description", "instructions", "default_worker",
            "fallback_enabled", "timeout_seconds", "profile", "result_format",
            "input_schema", "output_contract", "validation_policy",
        }
        out: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key == "fallback_enabled":
                out[key] = 1 if value else 0
            else:
                out[key] = value
        return out

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        request: dict[str, Any],
        *,
        name: str,
        description: str | None = None,
    ) -> TaskSpec:
        instructions = snapshot.get("task") or request.get("task") or ""
        worker = snapshot.get("worker") or request.get("worker") or "auto"
        fallback = snapshot.get("fallback")
        return cls(
            name=name,
            instructions=instructions,
            description=description,
            default_worker=worker,
            fallback_enabled=bool(fallback) if fallback is not None else True,
            timeout_seconds=snapshot.get("timeout_seconds") or request.get("timeout_seconds"),
            profile=snapshot.get("profile") or request.get("profile"),
            result_format=snapshot.get("result_format") or request.get("result_format"),
        )
```

- [ ] **Step 4: Run the test and commit**

Run: `python -m unittest tests.test_phase3_flows -v`
Expected: the TaskSpec tests pass.
Commit: `git commit -m "feat: add TaskSpec model with validation and snapshot promotion"`.

---

### Task 3: Engine orchestration (CRUD, run_task, save_run_as_task)

**Files:**
- Modify: `relay/engine.py` (`_task_snapshot`, `create_job`, new task methods)
- Modify: `tests/test_phase3_flows.py` (add the engine flow tests)

**Interfaces:**
- Consumes: Task 1 DB methods, Task 2 `TaskSpec`.
- Produces: `RelayEngine.create_task(spec) -> dict`, `update_task(task_id, **changes) -> dict`, `delete_task(task_id) -> bool`, `run_task(task_id, *, request=None, queued=False, submitted_via=None) -> tuple[dict, bool, dict]`, `save_run_as_task(run_id, *, name, description=None) -> dict`.
- Produces: `create_job` accepts `task_id` and `task_definition` keyword args.

- [ ] **Step 1: Write the failing flow tests**

Append to `tests/test_phase3_flows.py` a `TaskEngineFlowTests` class. File header imports: `json`, `tempfile`, `unittest`, `Path`, `Config`, `Database`, `RelayEngine`, `RelayError`, `JobRequest`, `TaskSpec`.

```python
class TaskEngineFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def _create_task(self, **overrides) -> dict:
        spec = TaskSpec(name="Weekly HBM report", instructions="Write the HBM report", **overrides)
        return self.engine.create_task(spec)

    def test_create_update_delete_task(self):
        task = self._create_task()
        self.assertEqual(task["version"], 1)
        updated = self.engine.update_task(task["task_id"], instructions="Updated report")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["instructions"], "Updated report")
        self.assertTrue(self.engine.delete_task(task["task_id"]))

    def test_run_task_stamps_task_id_and_snapshot(self):
        task = self._create_task(default_worker="codex", result_format="json")
        job, reused, task_ref = self.engine.run_task(task["task_id"], queued=True, submitted_via="cli")
        self.assertFalse(reused)
        self.assertEqual(job["task_id"], task["task_id"])
        snapshot = json.loads(job["task_snapshot_json"])
        self.assertEqual(snapshot["task_id"], task["task_id"])
        self.assertEqual(snapshot["task_version"], 1)
        self.assertEqual(snapshot["task_definition"]["instructions"], "Write the HBM report")

    def test_run_task_overrides_win_over_defaults(self):
        task = self._create_task(default_worker="codex")
        job, _, _ = self.engine.run_task(
            task["task_id"],
            request=JobRequest(task="Write the HBM report", worker="claude"),
            queued=True,
            submitted_via="cli",
        )
        self.assertEqual(job["requested_worker"], "claude")

    def test_editing_task_does_not_corrupt_past_run(self):
        task = self._create_task(instructions="v1 instructions")
        first, _, _ = self.engine.run_task(task["task_id"], queued=True, submitted_via="cli")
        self.engine.update_task(task["task_id"], instructions="v2 instructions")
        second, _, _ = self.engine.run_task(task["task_id"], queued=True, submitted_via="cli")
        first_snap = json.loads(first["task_snapshot_json"])
        second_snap = json.loads(second["task_snapshot_json"])
        self.assertEqual(first_snap["task_definition"]["instructions"], "v1 instructions")
        self.assertEqual(second_snap["task_definition"]["instructions"], "v2 instructions")
        self.assertEqual(first_snap["task_version"], 1)
        self.assertEqual(second_snap["task_version"], 2)

    def test_runs_for_task_returns_only_linked_runs(self):
        task = self._create_task()
        self.engine.run_task(task["task_id"], queued=True, submitted_via="cli")
        self.engine.create_job(JobRequest(task="Ad hoc", worker="codex"), queued=True, submitted_via="cli")
        runs = self.db.runs_for_task(task["task_id"])
        self.assertEqual(len(runs), 1)

    def test_save_run_as_task_derives_from_snapshot(self):
        job, _ = self.engine.create_job(
            JobRequest(task="Original ad hoc task", worker="codex"), queued=True, submitted_via="cli"
        )
        task = self.engine.save_run_as_task(job["job_id"], name="Saved Task", description="promoted")
        self.assertEqual(task["instructions"], "Original ad hoc task")
        self.assertEqual(task["default_worker"], "codex")
        self.assertEqual(task["version"], 1)
        self.assertIsNone(self.db.get_job(job["job_id"])["task_id"])

    def test_delete_task_preserves_runs(self):
        task = self._create_task()
        job, _, _ = self.engine.run_task(task["task_id"], queued=True, submitted_via="cli")
        self.engine.delete_task(task["task_id"])
        self.assertIsNone(self.db.get_task(task["task_id"]))
        self.assertEqual(self.db.get_job(job["job_id"])["task_id"], task["task_id"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_phase3_flows -v`
Expected: FAIL (no engine Task methods).

- [ ] **Step 3: Extend create_job and _task_snapshot**

In `relay/engine.py`, add two keyword args to `create_job`: `task_id: str | None = None` and `task_definition: dict[str, Any] | None = None`. In the `row` dict, replace `"task_id": None,` with `"task_id": task_id,`. Pass `task_definition=task_definition` into the `self._task_snapshot(...)` call.

Extend `_task_snapshot` to accept `task_definition` and merge it when present:

```python
        if task_definition:
            snapshot["task_id"] = task_definition.get("task_id")
            snapshot["task_name"] = task_definition.get("name")
            snapshot["task_version"] = task_definition.get("version")
            snapshot["task_definition"] = task_definition
```

- [ ] **Step 4: Add the engine Task methods**

```python
    def create_task(self, spec: TaskSpec) -> dict[str, Any]:
        row = spec.to_row()
        self.db.create_task(row)
        return self.db.get_task(row["task_id"])

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if not task:
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        normalized = TaskSpec.normalize_changes(changes)
        if not normalized:
            return task
        self.db.update_task(task_id, **normalized)
        return self.db.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        if not self.db.get_task(task_id):
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        return self.db.delete_task(task_id)

    def run_task(
        self,
        task_id: str,
        *,
        request: JobRequest | None = None,
        queued: bool = False,
        submitted_via: str | None = None,
    ) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        task = self.db.get_task(task_id)
        if not task:
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        instructions = task.get("instructions") or ""
        base = JobRequest(
            task=instructions,
            worker=task.get("default_worker") or "auto",
            fallback=bool(task.get("fallback_enabled", 1)) if task.get("fallback_enabled") is not None else None,
            timeout_seconds=task.get("timeout_seconds"),
            profile=task.get("profile") or "web-research",
            result_format=task.get("result_format") or "json",
        )
        if request:
            base.task = request.task or instructions
            base.worker = request.worker or base.worker
            base.result_format = request.result_format or base.result_format
            base.profile = request.profile or base.profile
            base.timeout_seconds = request.timeout_seconds or base.timeout_seconds
            base.fallback = request.fallback if request.fallback is not None else base.fallback
            base.attachments = list(request.attachments)
            base.artifact_inputs = list(request.artifact_inputs)
            base.request_id = request.request_id
            base.output_path = request.output_path
            base.artifact_path = request.artifact_path
            base.caller = request.caller
            base.model = request.model
        definition = {
            "task_id": task["task_id"],
            "name": task["name"],
            "version": task["version"],
            "instructions": instructions,
            "default_worker": task.get("default_worker"),
            "fallback_enabled": task.get("fallback_enabled"),
            "timeout_seconds": task.get("timeout_seconds"),
            "profile": task.get("profile"),
            "result_format": task.get("result_format"),
            "input_schema": task.get("input_schema"),
            "output_contract": task.get("output_contract"),
            "validation_policy": task.get("validation_policy"),
        }
        job, reused = self.create_job(
            base,
            queued=queued,
            submitted_via=submitted_via,
            task_id=task["task_id"],
            task_definition=definition,
        )
        return job, reused, task

    def save_run_as_task(self, run_id: str, *, name: str, description: str | None = None) -> dict[str, Any]:
        job = self.db.get_job(run_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {run_id}")
        snapshot: dict[str, Any] = {}
        if job.get("task_snapshot_json"):
            try:
                snapshot = json.loads(job["task_snapshot_json"])
            except json.JSONDecodeError:
                snapshot = {}
        request: dict[str, Any] = {}
        if job.get("request_json"):
            try:
                request = json.loads(job["request_json"])
            except json.JSONDecodeError:
                request = {}
        spec = TaskSpec.from_snapshot(snapshot, request, name=name, description=description)
        return self.create_task(spec)
```

Import `TaskSpec` at the top of `relay/engine.py` next to the `JobRequest` import.

- [ ] **Step 5: Run the focused tests and commit**

Run: `python -m unittest tests.test_phase3_flows -v`
Expected: PASS.
Run regression: `python -m unittest tests.test_phase0 tests.test_phase1 tests.test_phase2 tests.test_relay -v`
Expected: PASS.
Commit: `git commit -m "feat: add task lifecycle and run_task orchestration"`.

---

### Task 4: API functions

**Files:**
- Modify: `relay/api.py`
- Create: `tests/test_phase3_api.py`

**Interfaces:**
- Consumes: engine methods from Task 3, `Database` list/runs from Task 1.
- Produces: `list_tasks(engine)`, `create_task(engine, payload)`, `get_task(engine, task_id)`, `update_task(engine, task_id, payload)`, `delete_task(engine, task_id)`, `run_task(engine, task_id, payload)`, `runs_for_task(engine, task_id, *, limit=50)`, `save_run_as_task(engine, run_id, payload)`.

- [ ] **Step 1: Write failing API tests**

Create `tests/test_phase3_api.py` mirroring the engine flow tests but calling `relay.api` functions directly with a real engine/db. Cover: create returns a task, list filters, get raises `TASK_NOT_FOUND`, update bumps version, delete returns ok, run_task returns a job stamped with task_id, save_run_as_task derives instructions. Reuse the `setUp`/`tearDown` from the flow tests.

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_phase3_api -v`
Expected: FAIL (functions not defined).

- [ ] **Step 3: Add the API functions**

Append to `relay/api.py`:

```python
def _task_public(task: dict[str, Any]) -> dict[str, Any]:
    return {**task, "fallback_enabled": bool(task.get("fallback_enabled", 1))}


def list_tasks(engine) -> dict[str, Any]:
    return {"ok": True, "tasks": [_task_public(t) for t in engine.db.list_tasks(limit=200)]}


def create_task(engine, payload: dict[str, Any]) -> dict[str, Any]:
    from .models import TaskSpec

    spec = TaskSpec(
        name=str(payload.get("name") or "").strip(),
        instructions=payload.get("instructions") or payload.get("task") or "",
        description=payload.get("description"),
        default_worker=payload.get("default_worker") or payload.get("worker"),
        fallback_enabled=bool(payload.get("fallback_enabled", True)),
        timeout_seconds=payload.get("timeout_seconds"),
        profile=payload.get("profile"),
        result_format=payload.get("result_format") or payload.get("format"),
        input_schema=payload.get("input_schema"),
        output_contract=payload.get("output_contract"),
        validation_policy=payload.get("validation_policy"),
    )
    task = engine.create_task(spec)
    return {"ok": True, "task": _task_public(task)}


def get_task(engine, task_id: str) -> dict[str, Any]:
    task = engine.db.get_task(task_id)
    if not task:
        raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
    return {"ok": True, "task": _task_public(task)}


def update_task(engine, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    task = engine.update_task(task_id, **payload)
    return {"ok": True, "task": _task_public(task)}


def delete_task(engine, task_id: str) -> dict[str, Any]:
    engine.delete_task(task_id)
    return {"ok": True, "task_id": task_id, "deleted": True}


def run_task(engine, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .models import JobRequest

    overrides = payload.get("request") or {}
    request = None
    if overrides or payload.get("worker") or payload.get("format"):
        request = JobRequest(
            task=overrides.get("task") or "",
            worker=overrides.get("worker") or payload.get("worker") or "auto",
            result_format=overrides.get("result_format") or payload.get("format") or "json",
            profile=overrides.get("profile"),
            timeout_seconds=overrides.get("timeout_seconds"),
            attachments=list(overrides.get("attachments") or []),
            artifact_inputs=list(overrides.get("artifact_inputs") or []),
            request_id=overrides.get("request_id"),
            caller=overrides.get("caller", "human"),
        )
    job, reused, task = engine.run_task(
        task_id,
        request=request,
        queued=bool(payload.get("queued", False)),
        submitted_via=payload.get("submitted_via"),
    )
    return {"ok": True, "run": job, "reused": reused, "task": _task_public(task)}


def runs_for_task(engine, task_id: str, *, limit: int = 50) -> dict[str, Any]:
    if not engine.db.get_task(task_id):
        raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
    return {"ok": True, "task_id": task_id, "runs": engine.db.runs_for_task(task_id, limit=limit)}


def save_run_as_task(engine, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    task = engine.save_run_as_task(
        run_id,
        name=str(payload.get("name") or "").strip(),
        description=payload.get("description"),
    )
    return {"ok": True, "task": _task_public(task)}
```

- [ ] **Step 4: Run and commit**

Run: `python -m unittest tests.test_phase3_api -v`
Expected: PASS.
Commit: `git commit -m "feat: expose task API functions"`.

---

### Task 5: Daemon routes

**Files:**
- Modify: `relay/daemon.py` (GET handler, POST handler, DELETE handler, `/health` capabilities)

**Interfaces:**
- Consumes: API functions from Task 4.

- [ ] **Step 1: Write failing route tests**

Extend `tests/test_phase3_api.py` with a `DaemonTaskRouteTests` class that boots a daemon on a temp port (reuse the existing test daemon helper pattern from `tests/test_g0_api.py` / `tests/test_g3_api.py`) and asserts each endpoint returns the expected JSON shape and status codes (404 for missing task, 400 for invalid payload). At minimum add one end-to-end route test for `POST /v1/tasks` and `GET /v1/tasks`.

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_phase3_api -v`
Expected: FAIL (routes return 404).

- [ ] **Step 3: Wire the routes**

In `do_GET`, before the `path.startswith("/v1/runs/")` block, add `if path == "/v1/tasks": self._json(HTTPStatus.OK, list_tasks(self.daemon.engine)); return`. Add a `path.startswith("/v1/tasks/")` handler that distinguishes the `/runs` suffix (calls `runs_for_task`) from a bare task id (calls `get_task`), with 404 on `RelayError`.

In `do_POST`, add: `POST /v1/tasks` -> `create_task`; `POST /v1/runs/save-as-task` -> `save_run_as_task` (reads `run_id` from body); `POST /v1/tasks/{id}/run` -> `run_task`; `POST /v1/tasks/{id}` -> `update_task`.

Add a `do_DELETE` method (if absent) that authorizes, parses `path.startswith("/v1/tasks/")`, and calls `delete_task`.

Import the new API functions at the top of `relay/daemon.py`.

In `/health`, append `"task-registry"` and `"task-run"` to the `capabilities` list. Do not bump `api_schema_revision` (additive, matching Phase 2's approach).

- [ ] **Step 4: Run and commit**

Run: `python -m unittest tests.test_phase3_api tests.test_g0_api -v`
Expected: PASS.
Commit: `git commit -m "feat: route task daemon endpoints"`.

---

### Task 6: CLI commands

**Files:**
- Modify: `relay/cli.py` (new `task` subparsers, `run save-as-task` subparser, dispatch)

**Interfaces:**
- Consumes: daemon RPC for task operations; existing `_ensure_daemon` and `_emit` helpers.

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_phase3_cli.py` using the existing CLI test pattern (parse args, assert namespace fields). Cover: `task create --name Report --instructions "Write it"`; `task create --task-file PATH`; mutually exclusive `--fallback`/`--no-fallback`; `task run <id> --worker claude`; `run save-as-task <id> --name X`.

```python
    def test_task_create_parses_name_and_instructions(self):
        ns = build_parser().parse_args(["task", "create", "--name", "Report", "--instructions", "Write it"])
        self.assertEqual(ns.command, "task")
        self.assertEqual(ns.task_command, "create")
        self.assertEqual(ns.name, "Report")
        self.assertEqual(ns.instructions, "Write it")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m unittest tests.test_phase3_cli -v`
Expected: FAIL (unknown command).

- [ ] **Step 3: Add the subparsers and dispatch**

In `build_parser()`, add a `task` subparser group (follow the `_add_schedule_parsers` pattern) with `create`, `list`, `show`, `update`, `delete`, `run`, `runs` subcommands. Each supports `--machine`. `create` and `update` accept `--name`, `--instructions`, `--task-file`, `--worker`, mutually exclusive `--fallback`/`--no-fallback`, `--timeout`, `--profile`, `--format`, `--description`. `run` accepts run-time override args via `_add_request_args(run, task_required=False)` or a focused subset.

Add a `save-as-task` subcommand referencing a run id with `--name` and `--description`.

In `main()`, add dispatch branches for `command == "task"` and the `save-as-task` case that call the daemon RPC and emit via `_emit`.

- [ ] **Step 4: Run and commit**

Run: `python -m unittest tests.test_phase3_cli tests.test_g3_cli -v`
Expected: PASS.
Commit: `git commit -m "feat: add task CLI commands"`.

---

### Task 7: Integration, regression, and final verification

**Files:**
- Modify: `tests/test_phase3_flows.py`; optionally `RELEASE_NOTES.md`.

- [ ] **Step 1: Add an end-to-end test**

Add a test that creates a Task, runs it twice, edits it between runs, and asserts both Run snapshots reflect their respective versions (core completion criterion). `test_editing_task_does_not_corrupt_past_run` in Task 3 already covers this; add a sibling that runs `save_run_as_task` -> `run_task` -> compare.

- [ ] **Step 2: Run the full verification suite**

Run:

```
python -m unittest discover -s tests -v
python -m ruff format --check relay tests
python -m ruff check relay tests
python -m compileall -q relay tests
git diff --check
```

Expected: all green; Phase 0/1/2 and all G-series suites pass.

- [ ] **Step 3: Confirm scope and user files**

Run `git status --short --branch` and `git ls-files --others --exclude-standard`.
Expected: `relay-receipt.json`, `test_result.json`, `test_task.md` remain untracked; no unintended files staged.

- [ ] **Step 4: Commit the integration slice**

Commit: `git commit -m "test: add phase 3 task lifecycle integration coverage"` (if a new test was added).

## Stop Gate

- A Task can be created, run multiple times, and results compared via each Run's snapshot.
- Editing a Task bumps version; past Runs keep their original snapshot version and instructions.
- A Quick Run can be promoted to a stored Task via save-as-task without relinking the original Run.
- Deleting a Task preserves all Runs and their task_id reference.
- DB migrates 5 -> 6 additively; existing databases reopen cleanly with an empty tasks table.
- CLI, daemon API, and DB primitives each have focused tests; full suite, Ruff, and compileall pass.
- The `/health` capabilities list advertises `task-registry` and `task-run` without breaking the GUI compatibility floor.
- `relay-receipt.json`, `test_result.json`, and `test_task.md` are never staged.
