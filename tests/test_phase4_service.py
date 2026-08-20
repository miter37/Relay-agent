from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import TaskSpec
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

    def test_declared_interface_error_is_blocked_before_project_save(self):
        source = self.engine.create_task(
            TaskSpec(
                name="Source",
                instructions="source",
                output_contract=json.dumps({"outputs": [{"role": "report", "produces": ["text/plain"]}]}),
            )
        )
        target = self.engine.create_task(
            TaskSpec(
                name="Target",
                instructions="target",
                output_contract=json.dumps(
                    {"artifact_inputs": [{"name": "image", "accepts": ["image/png"]}]}
                ),
            )
        )
        definition = {
            "name": "Invalid binding",
            "nodes": [
                {"node_id": "source", "task_id": source["task_id"]},
                {"node_id": "target", "task_id": target["task_id"]},
            ],
            "connections": [
                {
                    "from_node": "source",
                    "from_output": "report",
                    "to_node": "target",
                    "to_input": "image",
                }
            ],
            "output_selection": [],
        }
        with self.assertRaisesRegex(RelayError, "PROJECT_INTERFACE_INVALID") as ctx:
            self.service.create_project(definition)
        self.assertEqual(ctx.exception.details["errors"][0]["code"], "CONNECTION_FORMAT_INCOMPATIBLE")


if __name__ == "__main__":
    unittest.main()
