from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest, TaskSpec


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
        base = {"name": "Weekly HBM report", "instructions": "Write the HBM report"}
        base.update(overrides)
        spec = TaskSpec(**base)
        return self.engine.create_task(spec)

    def test_create_update_delete_task(self):
        task = self._create_task()
        self.assertEqual(task["version"], 1)
        updated = self.engine.update_task(task["task_id"], instructions="Updated report")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["instructions"], "Updated report")
        self.assertTrue(self.engine.delete_task(task["task_id"]))

    def test_registered_task_summary_is_stored_and_pinned_in_run_snapshot(self):
        task = self._create_task(task_summary="Collect and summarize weekly HBM evidence.")
        self.assertEqual(task["task_summary"], "Collect and summarize weekly HBM evidence.")
        job, _, _ = self.engine.run_task(task["task_id"], queued=True, submitted_via="cli")
        snapshot = json.loads(job["task_snapshot_json"])
        self.assertEqual(snapshot["task_definition"]["task_summary"], task["task_summary"])
        self.assertEqual(job["task_summary"], task["task_summary"])

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

    def test_registered_task_run_accepts_optional_inputs_and_pins_them(self):
        task = self._create_task(
            input_schema=json.dumps(
                {
                    "type": "object",
                    "required": ["period"],
                    "properties": {"period": {"type": "string"}},
                    "additionalProperties": False,
                }
            )
        )
        job, _, _ = self.engine.run_task(
            task["task_id"],
            request=JobRequest(task="", inputs={"period": "previous-week"}),
            queued=True,
            submitted_via="cli",
        )
        self.assertEqual(json.loads(job["request_json"])["inputs"], {"period": "previous-week"})
        self.assertEqual(json.loads(job["task_snapshot_json"])["inputs"], {"period": "previous-week"})

    def test_registered_task_rejects_invalid_optional_inputs(self):
        task = self._create_task(input_schema=json.dumps({"type": "object", "required": ["period"]}))
        with self.assertRaisesRegex(RelayError, "INPUT_SCHEMA_MISMATCH"):
            self.engine.run_task(
                task["task_id"],
                request=JobRequest(task="", inputs={}),
                queued=True,
                submitted_via="cli",
            )

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


if __name__ == "__main__":
    unittest.main()
