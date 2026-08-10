from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from relay.db import CURRENT_SCHEMA_VERSION, Database
from relay.errors import RelayError
from relay.projects.models import ProjectNode, ProjectOutputSelection, ProjectSpec


def _spec(**kwargs) -> ProjectSpec:
    defaults = dict(
        nodes=[ProjectNode(node_id="a", task_id="task-a")],
        connections=[],
        output_selection=ProjectOutputSelection(items=[]),
        name="Spec",
    )
    defaults.update(kwargs)
    return ProjectSpec(**defaults)


class OrchestratorSpecTests(unittest.TestCase):
    def test_orchestrator_none_leaves_snapshot_unchanged(self):
        with_orchestrator = _spec(orchestrator=None)
        without_field = _spec()
        self.assertEqual(with_orchestrator.to_snapshot(), without_field.to_snapshot())
        self.assertNotIn("orchestrator", json.loads(with_orchestrator.to_snapshot()))

    def test_orchestrator_round_trips_through_snapshot(self):
        spec = _spec(orchestrator={"enabled": True, "worker": "claude", "max_llm_calls_per_run": 4})
        payload = json.loads(spec.to_snapshot())
        self.assertEqual(payload["orchestrator"], {"enabled": True, "worker": "claude", "max_llm_calls_per_run": 4})
        restored = ProjectSpec.from_dict(payload)
        self.assertEqual(restored.orchestrator, spec.orchestrator)

    def test_orchestrator_rejects_unknown_keys(self):
        spec = _spec(orchestrator={"enabled": True, "bogus": 1})
        with self.assertRaises(RelayError) as ctx:
            spec.validate(lambda task_id: {"task_id": task_id})
        self.assertEqual(ctx.exception.code, "PROJECT_INVALID")

    def test_orchestrator_rejects_non_positive_budget(self):
        spec = _spec(orchestrator={"enabled": True, "max_repair_attempts_per_run": 0})
        with self.assertRaises(RelayError):
            spec.validate(lambda task_id: {"task_id": task_id})

    def test_orchestrator_rejects_non_bool_enabled(self):
        spec = _spec(orchestrator={"enabled": "yes"})
        with self.assertRaises(RelayError):
            spec.validate(lambda task_id: {"task_id": task_id})

    def test_orchestrator_valid_configuration_passes(self):
        spec = _spec(
            orchestrator={
                "enabled": True,
                "worker": "claude",
                "profile": "default",
                "max_repair_attempts_per_node": 2,
                "max_repair_attempts_per_run": 6,
                "max_llm_calls_per_run": 8,
            }
        )
        spec.validate(lambda task_id: {"task_id": task_id})  # must not raise


class MigrationV14ToV15Tests(unittest.TestCase):
    def _make_v14_db_with_legacy_override(self, path: Path) -> None:
        """Build a v14 DB with one Project Run step carrying the legacy worker_override shape."""
        db = Database(path)
        db.create_project(
            {
                "project_id": "proj-1",
                "name": "Proj",
                "description": None,
                "definition_json": json.dumps(
                    {
                        "name": "Proj",
                        "nodes": [{"node_id": "a", "task_id": "task-a"}],
                        "connections": [],
                        "output_selection": [],
                    }
                ),
                "project_summary": "Proj",
            }
        )
        db.create_project_run(
            {
                "project_run_id": "run-1",
                "project_id": "proj-1",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "failed",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )
        db.create_or_update_project_step(
            {
                "project_run_id": "run-1",
                "node_id": "a",
                "task_id": "task-a",
                "task_version": 1,
                "status": "failed",
            }
        )
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(
                "UPDATE project_run_steps SET resolved_connections_json=? "
                "WHERE project_run_id='run-1' AND node_id='a'",
                (json.dumps({"worker_override": "claude"}),),
            )
            conn.execute("ALTER TABLE project_run_steps DROP COLUMN step_overrides_json")
            conn.execute("DROP TABLE IF EXISTS project_run_events")
            conn.execute("DROP TABLE IF EXISTS project_run_orchestrator_state")
            conn.execute("PRAGMA user_version=14")

    def test_migration_adds_tables_and_column_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            self._make_v14_db_with_legacy_override(path)

            Database(path)

            with closing(sqlite3.connect(path)) as conn, conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_SCHEMA_VERSION)
                cols = {row[1] for row in conn.execute("PRAGMA table_info(project_run_steps)").fetchall()}
                self.assertIn("step_overrides_json", cols)
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                self.assertIn("project_run_events", tables)
                self.assertIn("project_run_orchestrator_state", tables)
                # Prior row (task/project) survived untouched.
                step = conn.execute(
                    "SELECT status, task_id FROM project_run_steps WHERE project_run_id='run-1' AND node_id='a'"
                ).fetchone()
                self.assertEqual(tuple(step), ("failed", "task-a"))

    def test_migration_backfills_legacy_worker_override_into_step_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            self._make_v14_db_with_legacy_override(path)

            Database(path)

            with closing(sqlite3.connect(path)) as conn, conn:
                row = conn.execute(
                    "SELECT resolved_connections_json, step_overrides_json FROM project_run_steps "
                    "WHERE project_run_id='run-1' AND node_id='a'"
                ).fetchone()
                resolved_json, overrides_json = row
                self.assertIsNone(resolved_json)
                self.assertEqual(json.loads(overrides_json), {"worker_override": "claude"})

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            self._make_v14_db_with_legacy_override(path)
            Database(path)
            Database(path)  # second open must not raise or duplicate-backfill
            with closing(sqlite3.connect(path)) as conn, conn:
                row = conn.execute(
                    "SELECT step_overrides_json FROM project_run_steps WHERE project_run_id='run-1' AND node_id='a'"
                ).fetchone()
                self.assertEqual(json.loads(row[0]), {"worker_override": "claude"})

    def test_new_database_has_orchestrator_tables_and_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relay.db"
            Database(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(project_run_steps)").fetchall()}
                self.assertIn("step_overrides_json", cols)
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                self.assertIn("project_run_events", tables)
                self.assertIn("project_run_orchestrator_state", tables)


class OrchestratorEventStorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "relay.db"
        self.db = Database(path)
        self.db.create_project(
            {
                "project_id": "proj-1",
                "name": "Proj",
                "description": None,
                "definition_json": json.dumps(
                    {"name": "Proj", "nodes": [{"node_id": "a", "task_id": "task-a"}], "connections": [], "output_selection": []}
                ),
                "project_summary": "Proj",
            }
        )
        self.db.create_project_run(
            {
                "project_run_id": "run-1",
                "project_id": "proj-1",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "running",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )

    def test_events_are_returned_in_seq_order(self):
        self.db.append_project_run_event(
            "run-1", node_id=None, kind="note", actor="runtime", summary="Run started."
        )
        self.db.append_project_run_event(
            "run-1", node_id="a", kind="decision", actor="orchestrator", summary="Retrying a.", detail={"x": 1}
        )
        self.db.append_project_run_event(
            "run-1", node_id="a", kind="repair", actor="orchestrator", summary="Repaired a."
        )
        events = self.db.list_project_run_events("run-1")
        self.assertEqual([e["kind"] for e in events], ["note", "decision", "repair"])
        self.assertEqual([e["seq"] for e in events], [1, 2, 3])
        self.assertEqual(events[1]["detail_json"], json.dumps({"x": 1}))

    def test_events_scoped_to_project_run(self):
        self.db.create_project_run(
            {
                "project_run_id": "run-2",
                "project_id": "proj-1",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "running",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )
        self.db.append_project_run_event("run-1", node_id=None, kind="note", actor="runtime", summary="A")
        self.db.append_project_run_event("run-2", node_id=None, kind="note", actor="runtime", summary="B")
        self.assertEqual(len(self.db.list_project_run_events("run-1")), 1)
        self.assertEqual(len(self.db.list_project_run_events("run-2")), 1)

    def test_orchestrator_state_defaults_to_none(self):
        self.assertIsNone(self.db.get_orchestrator_state("run-1"))

    def test_orchestrator_state_upsert_accumulates(self):
        self.db.upsert_orchestrator_state("run-1", llm_calls_used=1, repair_attempts_used=1, state_digest="d1")
        self.db.upsert_orchestrator_state("run-1", llm_calls_used=2, repair_attempts_used=1, state_digest="d2")
        state = self.db.get_orchestrator_state("run-1")
        self.assertEqual(state["llm_calls_used"], 2)
        self.assertEqual(state["repair_attempts_used"], 1)
        self.assertEqual(state["state_digest"], "d2")


if __name__ == "__main__":
    unittest.main()
