from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import TaskSpec
from relay.projects.models import (
    ProjectConnection,
    ProjectNode,
    ProjectOutputSelection,
    ProjectSpec,
)
from relay.projects.service import ProjectService


def _make_task(engine: RelayEngine, name: str, instructions: str = "do") -> dict:
    spec = TaskSpec(name=name, instructions=instructions)
    return engine.create_task(spec)


class ProjectServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def _seq_def(self) -> dict:
        _make_task(self.engine, "A", "alpha")
        _make_task(self.engine, "B", "bravo")
        return {
            "name": "Seq",
            "description": "two-step",
            "failure_policy": "stop",
            "nodes": [
                {"node_id": "a", "task_id": _make_task(self.engine, "A2", "A")["task_id"]},
                {"node_id": "b", "task_id": _make_task(self.engine, "B2", "B")["task_id"]},
            ],
            "connections": [],
            "output_selection": [],
        }

    def _missing_task_def(self) -> dict:
        return {
            "name": "Missing",
            "nodes": [{"node_id": "a", "task_id": "no-such-task"}],
            "connections": [],
            "output_selection": [],
        }

    def test_create_project_validates_task_existence(self):
        with self.assertRaisesRegex(RelayError, "TASK_MISSING"):
            self.service.create_project(self._missing_task_def())

    def test_create_project_persists_under_soft_delete(self):
        project = self.service.create_project(self._seq_def())
        listed = self.service.list_projects()
        self.assertIn(project["project_id"], [p["project_id"] for p in listed])

    def test_soft_delete_project_preserves_history(self):
        project = self.service.create_project(self._seq_def())
        pid = project["project_id"]
        self.service.soft_delete_project(pid)
        with self.assertRaisesRegex(RelayError, "PROJECT_NOT_FOUND"):
            self.service.get_project(pid)


if __name__ == "__main__":
    unittest.main()
