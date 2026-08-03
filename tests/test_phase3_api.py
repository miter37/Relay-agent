from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.api import (
    create_task,
    delete_task,
    get_task,
    list_tasks,
    run_task,
    runs_for_task,
    save_run_as_task,
    update_task,
)
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest


class TaskAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_api_create_list_get_update_delete(self):
        created = create_task(self.engine, {"name": "Weekly HBM report", "instructions": "Write report"})
        self.assertTrue(created["ok"])
        task_id = created["task"]["task_id"]

        listed = list_tasks(self.engine)
        self.assertEqual(len(listed["tasks"]), 1)
        self.assertEqual(listed["tasks"][0]["name"], "Weekly HBM report")

        fetched = get_task(self.engine, task_id)
        self.assertEqual(fetched["task"]["instructions"], "Write report")

        updated = update_task(self.engine, task_id, {"instructions": "Updated report"})
        self.assertEqual(updated["task"]["instructions"], "Updated report")
        self.assertEqual(updated["task"]["version"], 2)

        deleted = delete_task(self.engine, task_id)
        self.assertTrue(deleted["deleted"])
        with self.assertRaisesRegex(RelayError, "TASK_NOT_FOUND"):
            get_task(self.engine, task_id)

    def test_api_run_task_and_runs_for_task(self):
        task = create_task(self.engine, {"name": "HBM", "instructions": "HBM analysis"})["task"]
        run_res = run_task(self.engine, task["task_id"], {"queued": True, "submitted_via": "cli"})
        self.assertTrue(run_res["ok"])
        self.assertEqual(run_res["run"]["task_id"], task["task_id"])

        runs = runs_for_task(self.engine, task["task_id"])
        self.assertEqual(len(runs["runs"]), 1)
        self.assertEqual(runs["runs"][0]["job_id"], run_res["run"]["job_id"])

    def test_api_save_run_as_task(self):
        job, _ = self.engine.create_job(
            JobRequest(task="Ad-hoc request", worker="codex"), queued=True, submitted_via="cli"
        )
        promoted = save_run_as_task(self.engine, job["job_id"], {"name": "Promoted Task"})
        self.assertTrue(promoted["ok"])
        self.assertEqual(promoted["task"]["instructions"], "Ad-hoc request")


if __name__ == "__main__":
    unittest.main()
