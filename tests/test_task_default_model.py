"""Task.default_model: registered Tasks previously had no way to pin a model, so a
Project node dispatched through ProjectRuntime._dispatch_step -> engine.run_task_from_snapshot
could not honor a user's requested model (discovered during real Orchestrator usage,
2026-08-10). Mirrors the existing default_worker field end to end: schema, TaskSpec,
engine dispatch (both run_task and run_task_from_snapshot), and CLI plumbing.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from relay.config import Config
from relay.db import CURRENT_SCHEMA_VERSION, Database
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec


class MigrationV15ToV16Tests(unittest.TestCase):
    def test_v15_to_v16_adds_default_model_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            db = Database(path)
            db.create_task(
                {
                    "task_id": "legacy-task",
                    "name": "Legacy",
                    "instructions": "do it",
                    "default_worker": "claude",
                }
            )
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("ALTER TABLE tasks DROP COLUMN default_model")
                conn.execute("PRAGMA user_version=15")

            Database(path)

            with closing(sqlite3.connect(path)) as conn, conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
                cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
                self.assertIn("default_model", cols)
                row = conn.execute("SELECT name, default_worker FROM tasks WHERE task_id='legacy-task'").fetchone()
                self.assertEqual(tuple(row), ("Legacy", "claude"))

    def test_new_database_has_default_model_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
                self.assertIn("default_model", cols)


class TaskSpecDefaultModelTests(unittest.TestCase):
    def test_to_row_includes_default_model(self):
        spec = TaskSpec(name="A", instructions="do A", default_model="gpt-5.6-luna")
        row = spec.to_row()
        self.assertEqual(row["default_model"], "gpt-5.6-luna")

    def test_to_row_default_model_defaults_to_none(self):
        spec = TaskSpec(name="A", instructions="do A")
        row = spec.to_row()
        self.assertIsNone(row["default_model"])

    def test_normalize_changes_allows_default_model(self):
        changes = TaskSpec.normalize_changes({"default_model": "gpt-5.6-luna", "bogus": 1})
        self.assertEqual(changes, {"default_model": "gpt-5.6-luna"})


class _EngineHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()


class RunTaskModelThreadingTests(_EngineHarness):
    def test_run_task_uses_task_default_model_when_request_omits_it(self):
        task = self.engine.create_task(
            TaskSpec(name="A", instructions="do A", default_worker="codex", default_model="gpt-5.6-luna")
        )
        job, _reused, _task = self.engine.run_task(task["task_id"], queued=True, caller="service")
        stored_request = self.db.get_job(job["job_id"])
        self.assertEqual(stored_request["requested_worker"], "codex")
        import json

        self.assertEqual(json.loads(stored_request["request_json"])["model"], "gpt-5.6-luna")

    def test_run_task_explicit_request_model_overrides_task_default(self):
        task = self.engine.create_task(TaskSpec(name="A", instructions="do A", default_model="gpt-5.6-luna"))
        job, _reused, _task = self.engine.run_task(
            task["task_id"],
            request=JobRequest(task="do A", model="gpt-5.6-terra"),
            queued=True,
            caller="service",
        )
        import json

        self.assertEqual(json.loads(self.db.get_job(job["job_id"])["request_json"])["model"], "gpt-5.6-terra")


class RunTaskFromSnapshotModelThreadingTests(_EngineHarness):
    """This is the exact path ProjectRuntime._dispatch_step uses - the gap a real
    Project node dispatch actually hit."""

    def test_snapshot_default_model_reaches_the_dispatched_request(self):
        task = self.engine.create_task(
            TaskSpec(
                name="A", instructions="do A", default_worker="antigravity", default_model="gemini-3.6-flash-medium"
            )
        )
        snapshot = self.engine.load_task_for_snapshot(task["task_id"])
        self.assertEqual(snapshot["default_model"], "gemini-3.6-flash-medium")

        request = JobRequest(task="do A", caller="service", worker="antigravity", artifact_inputs=[])
        job, _reused = self.engine.run_task_from_snapshot(
            snapshot, request=request, queued=True, submitted_via="project", caller="service"
        )
        import json

        stored = json.loads(self.db.get_job(job["job_id"])["request_json"])
        self.assertEqual(stored["model"], "gemini-3.6-flash-medium")
        self.assertEqual(stored["worker"], "antigravity")

    def test_explicit_request_model_overrides_snapshot_default(self):
        task = self.engine.create_task(TaskSpec(name="A", instructions="do A", default_model="gemini-3.6-flash-medium"))
        snapshot = self.engine.load_task_for_snapshot(task["task_id"])
        request = JobRequest(task="do A", caller="service", worker="antigravity", model="gemini-3.6-flash-high")
        job, _reused = self.engine.run_task_from_snapshot(
            snapshot, request=request, queued=True, submitted_via="project", caller="service"
        )
        import json

        self.assertEqual(json.loads(self.db.get_job(job["job_id"])["request_json"])["model"], "gemini-3.6-flash-high")

    def test_no_default_model_leaves_model_none(self):
        task = self.engine.create_task(TaskSpec(name="A", instructions="do A"))
        snapshot = self.engine.load_task_for_snapshot(task["task_id"])
        request = JobRequest(task="do A", caller="service", worker="antigravity", artifact_inputs=[])
        job, _reused = self.engine.run_task_from_snapshot(
            snapshot, request=request, queued=True, submitted_via="project", caller="service"
        )
        import json

        self.assertIsNone(json.loads(self.db.get_job(job["job_id"])["request_json"])["model"])


class ProjectRuntimeDispatchModelTests(_EngineHarness):
    """The actual path a real Project Run takes: ProjectRuntime._dispatch_step, not a
    direct run_task_from_snapshot call."""

    def test_project_node_dispatch_honors_task_default_model(self):
        import json

        from relay.projects.runtime import ProjectRuntime
        from relay.projects.service import ProjectService

        task = self.engine.create_task(
            TaskSpec(
                name="A", instructions="do A", default_worker="antigravity", default_model="gemini-3.6-flash-medium"
            )
        )
        service = ProjectService(self.db, self.engine)
        runtime = ProjectRuntime(self.db, self.engine, service)
        self.addCleanup(runtime.stop)
        project = service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run = service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        runtime.tick_once()

        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        self.assertTrue(job_id)
        stored = json.loads(self.db.get_job(job_id)["request_json"])
        self.assertEqual(stored["model"], "gemini-3.6-flash-medium")
        self.assertEqual(stored["worker"], "antigravity")


if __name__ == "__main__":
    unittest.main()
