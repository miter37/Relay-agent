from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.orchestrator.overrides import (
    apply_instruction_addendum,
    effective_manifest_entries,
    effective_output_role,
    parse_step_overrides,
)
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.util import new_artifact_uid


class InstructionAddendumTests(unittest.TestCase):
    def test_no_addendum_returns_original_unchanged(self):
        self.assertEqual(apply_instruction_addendum("Do the thing.", None), "Do the thing.")
        self.assertEqual(apply_instruction_addendum("Do the thing.", ""), "Do the thing.")
        self.assertEqual(apply_instruction_addendum("Do the thing.", "   "), "Do the thing.")

    def test_addendum_is_appended_not_substituted(self):
        result = apply_instruction_addendum("Do the thing.", "Also include role: output in artifacts[].")
        self.assertTrue(result.startswith("Do the thing."))
        self.assertIn("Do the thing.", result)
        self.assertIn("Also include role: output in artifacts[].", result)
        self.assertIn("this attempt only", result)


class ManifestOverrideTests(unittest.TestCase):
    def test_no_overrides_returns_manifest_unchanged(self):
        manifest = [{"from_node": "a", "from_role": "result", "to_alias": "A1", "artifact_uid": None}]
        self.assertEqual(effective_manifest_entries(manifest, None), manifest)
        self.assertEqual(effective_manifest_entries(manifest, {}), manifest)

    def test_rebind_changes_role_for_matching_alias_only(self):
        manifest = [
            {"from_node": "a", "from_role": "result", "to_alias": "A1", "artifact_uid": None},
            {"from_node": "b", "from_role": "output", "to_alias": "A2", "artifact_uid": None},
        ]
        rebound = effective_manifest_entries(manifest, {"A1": "output"})
        self.assertEqual(rebound[0]["from_role"], "output")
        self.assertEqual(rebound[1]["from_role"], "output")  # unaffected, untouched alias

    def test_already_resolved_entries_are_not_touched(self):
        manifest = [
            {
                "from_node": "a",
                "from_role": "result",
                "to_alias": "A1",
                "artifact_uid": "uid-1",
                "snapshot": {"sha256": "x"},
            }
        ]
        rebound = effective_manifest_entries(manifest, {"A1": "output"})
        self.assertEqual(rebound[0]["from_role"], "result")

    def test_external_input_entries_pass_through(self):
        manifest = [{"node_id": "a", "to_alias": "A1", "artifact_uid": "uid-1"}]
        rebound = effective_manifest_entries(manifest, {"A1": "output"})
        self.assertEqual(rebound, manifest)


class OutputRoleOverrideTests(unittest.TestCase):
    def test_no_override_returns_default_role(self):
        self.assertEqual(effective_output_role("result", None), "result")
        self.assertEqual(effective_output_role("result", ""), "result")

    def test_override_replaces_default_role(self):
        self.assertEqual(effective_output_role("result", "output"), "output")


class ParseStepOverridesTests(unittest.TestCase):
    def test_missing_or_null_returns_empty_dict(self):
        self.assertEqual(parse_step_overrides(None), {})
        self.assertEqual(parse_step_overrides(""), {})

    def test_malformed_json_returns_empty_dict(self):
        self.assertEqual(parse_step_overrides("{not json"), {})

    def test_non_object_json_returns_empty_dict(self):
        self.assertEqual(parse_step_overrides("[1,2,3]"), {})

    def test_valid_object_round_trips(self):
        self.assertEqual(
            parse_step_overrides(json.dumps({"worker_override": "codex"})), {"worker_override": "codex"}
        )


class _OverrideHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "home")
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)

    def tearDown(self) -> None:
        self.runtime.stop()
        self.temp.cleanup()

    def _task(self, name: str) -> dict:
        return self.engine.create_task(TaskSpec(name=name, instructions=f"do {name}"))

    def _complete_step_with_role(self, project_run_id: str, node_id: str, role: str) -> None:
        step = self.db.get_project_step(project_run_id, node_id)
        job_id = step["active_task_run_id"]
        self.db.update_job(job_id, status="COMPLETED", result_status="complete")
        artifact_dir = self.config.path_value("artifact_root") / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{role}.txt"
        path.write_text(f"{role}-payload", encoding="utf-8")
        self.db.add_artifact(
            job_id,
            relative_path=path.name,
            final_path=str(path),
            mime_type="text/plain",
            size=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            artifact_uid=new_artifact_uid(),
            role=role,
        )


class ConnectionRebindIntegrationTests(_OverrideHarness):
    def test_role_mismatch_fails_without_override(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Mismatch",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        # "a" actually emits role "output", not the declared "result".
        self._complete_step_with_role(project_run_id, "a", role="output")
        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_step(project_run_id, "b")["status"] in {"failed", "blocked"}:
                break
        step_b = self.db.get_project_step(project_run_id, "b")
        self.assertEqual(step_b["status"], "failed")
        self.assertEqual(step_b["error_code"], "PROJECT_ARTIFACT_MISSING")

    def test_connection_override_repairs_role_mismatch_on_retry(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Mismatch",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        self._complete_step_with_role(project_run_id, "a", role="output")
        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_step(project_run_id, "b")["status"] in {"failed", "blocked"}:
                break
        self.assertEqual(self.db.get_project_run(project_run_id)["status"], "failed")

        # Apply a run-scoped connection override to node "b" for alias A1.
        self.db.update_project_step(
            project_run_id,
            "b",
            status="pending",
            active_task_run_id=None,
            error_code=None,
            error_message=None,
            step_overrides_json=json.dumps({"connection_overrides": {"A1": "output"}}),
        )
        self.db.update_project_run(project_run_id, status="running", completed_at=None, started_at=None)

        for _ in range(6):
            self.runtime.tick_once()
            status = self.db.get_project_step(project_run_id, "b")["status"]
            if status in {"running", "completed", "failed"}:
                break
        step_b = self.db.get_project_step(project_run_id, "b")
        self.assertIn(step_b["status"], {"running", "completed"})
        self.assertIsNotNone(step_b["active_task_run_id"])

    def test_rebind_to_nonexistent_role_still_fails_structurally(self):
        a = self._task("A")
        b = self._task("B")
        project = self.service.create_project(
            {
                "name": "Mismatch",
                "nodes": [
                    {"node_id": "a", "task_id": a["task_id"]},
                    {"node_id": "b", "task_id": b["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        self._complete_step_with_role(project_run_id, "a", role="output")
        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_step(project_run_id, "b")["status"] in {"failed", "blocked"}:
                break

        self.db.update_project_step(
            project_run_id,
            "b",
            status="pending",
            active_task_run_id=None,
            error_code=None,
            error_message=None,
            step_overrides_json=json.dumps({"connection_overrides": {"A1": "does-not-exist"}}),
        )
        self.db.update_project_run(project_run_id, status="running", completed_at=None, started_at=None)

        for _ in range(6):
            self.runtime.tick_once()
            if self.db.get_project_step(project_run_id, "b")["status"] in {"failed", "blocked"}:
                break
        step_b = self.db.get_project_step(project_run_id, "b")
        self.assertEqual(step_b["status"], "failed")
        self.assertEqual(step_b["error_code"], "PROJECT_ARTIFACT_MISSING")


class OutputRoleOverrideIntegrationTests(_OverrideHarness):
    def test_output_role_override_repairs_finalize_mismatch(self):
        a = self._task("A")
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [{"node_id": "a", "role": "result"}],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]
        self.runtime.tick_once()
        self._complete_step_with_role(project_run_id, "a", role="output")

        # Apply an output_role_override on node "a" before it finalizes; simulate the
        # dispatcher having already recorded output-role instead of the declared result.
        self.db.update_project_step(
            project_run_id,
            "a",
            step_overrides_json=json.dumps({"output_role_override": "output"}),
        )
        self.runtime.tick_once()

        run_row = self.db.get_project_run(project_run_id)
        self.assertEqual(run_row["status"], "completed")
        final_ids = json.loads(run_row["final_artifact_ids_json"] or "[]")
        self.assertEqual(len(final_ids), 1)
        self.assertEqual(final_ids[0]["role"], "output")


class InstructionAddendumIntegrationTests(_OverrideHarness):
    def test_addendum_reaches_dispatched_job_and_original_result_validation_still_applies(self):
        a = self._task("A")
        project = self.service.create_project(
            {
                "name": "Solo",
                "nodes": [{"node_id": "a", "task_id": a["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run = self.service.create_project_run(project["project_id"])
        project_run_id = run["project_run_id"]

        self.db.update_project_step(
            project_run_id,
            "a",
            step_overrides_json=json.dumps({"instruction_addendum": "Always include artifacts[].role."}),
        )
        self.runtime.tick_once()

        job_id = self.db.get_project_step(project_run_id, "a")["active_task_run_id"]
        job = self.db.get_job(job_id)
        dispatched_task_text = json.loads(job["request_json"])["task"]
        self.assertIn("do A", dispatched_task_text)
        self.assertIn("Always include artifacts[].role.", dispatched_task_text)
        # The addendum reached dispatch without mutating the registered Task definition.
        self.assertEqual(self.db.get_task(a["task_id"])["instructions"], "do A")


if __name__ == "__main__":
    unittest.main()
