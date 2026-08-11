from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from relay.api import (
    catalog_capability,
    catalog_project_runs,
    catalog_projects,
    catalog_task_runs,
    catalog_tasks,
    get_task,
    project_runs,
    run_lineage,
)
from relay.cli import build_parser
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest, TaskSpec
from relay.projects.service import ProjectService
from relay.rpc import RPCClient
from relay.util import new_artifact_uid, sha256_file


class CatalogApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_capability_manifest_is_stable(self):
        capability = catalog_capability()

        self.assertEqual(capability["catalog_schema_version"], 1)
        self.assertEqual(set(capability["kinds"]), {"tasks", "task_runs", "projects", "project_runs"})
        self.assertEqual(capability["response_contract"]["list_items_key"], "items")
        self.assertEqual(capability["resources"]["artifact_content"]["text_field"], "text")
        self.assertEqual(capability["kinds"]["tasks"]["order"], "updated_at_desc")

    def test_task_catalog_is_bounded_and_cursor_is_tie_safe(self):
        first = self.engine.create_task(
            TaskSpec(name="First", instructions="private prompt one", task_summary="First summary")
        )
        second = self.engine.create_task(
            TaskSpec(name="Second", instructions="private prompt two", task_summary="Second summary")
        )
        same_time = "2026-08-01T00:00:00+00:00"
        self.db.update_task(first["task_id"], updated_at=same_time)
        self.db.update_task(second["task_id"], updated_at=same_time)

        page = catalog_tasks(self.db, limit=1)
        self.assertEqual(len(page["tasks"]), 1)
        self.assertEqual(page["kind"], "tasks")
        self.assertIs(page["items"], page["tasks"])
        self.assertTrue(page["has_more"])
        self.assertNotIn("instructions", page["tasks"][0])

        next_page = catalog_tasks(self.db, limit=1, cursor=page["next_cursor"])
        self.assertEqual(len(next_page["tasks"]), 1)
        self.assertNotEqual(page["tasks"][0]["task_id"], next_page["tasks"][0]["task_id"])

        with self.assertRaises(RelayError) as raised:
            catalog_tasks(self.db, cursor="not-a-cursor")
        self.assertEqual(raised.exception.code, "INVALID_CURSOR")

    def test_task_run_catalog_contains_summaries_and_artifact_metadata_only(self):
        task = self.engine.create_task(
            TaskSpec(name="Catalog task", instructions="private instructions", task_summary="Task summary")
        )
        job, _, _ = self.engine.run_task(task["task_id"], request=JobRequest(task="", worker="codex"))
        output = Path(job["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"summary":"Result summary"}', encoding="utf-8")
        self.db.update_job(
            job["job_id"],
            status="COMPLETED",
            actual_worker="codex",
            result_summary="Result summary",
            completed_at="2026-08-02T00:00:00+00:00",
        )
        self.db.add_artifact(
            job["job_id"],
            relative_path="result.txt",
            final_path=str(output),
            mime_type="text/plain",
            size=16,
            sha256="a" * 64,
            role="final_report",
        )

        result = catalog_task_runs(self.db, status="completed", task_id=task["task_id"])
        self.assertEqual(len(result["task_runs"]), 1)
        self.assertEqual(result["kind"], "task_runs")
        self.assertIs(result["items"], result["task_runs"])
        item = result["task_runs"][0]
        self.assertEqual(item["task_run_id"], job["job_id"])
        self.assertEqual(item["task_version"], 1)
        self.assertEqual(item["task_summary"], "Task summary")
        self.assertEqual(item["result_summary"], "Result summary")
        self.assertEqual(item["artifact_roles"], ["final_report"])
        self.assertTrue(item["result_available"])
        self.assertNotIn("request_json", item)
        self.assertNotIn("task_snapshot_json", item)

    def test_failed_run_exposes_bounded_failure_reason(self):
        job, _ = self.engine.create_job(JobRequest(task="will fail", worker="codex"))
        self.db.update_job(job["job_id"], status="FAILED", error_message="worker failed")

        item = catalog_task_runs(self.db)["task_runs"][0]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["failure_reason"], "worker failed")

    def test_catalog_to_task_selection_receipt_and_artifact_reuse(self):
        source_task = self.engine.create_task(
            TaskSpec(
                name="Source report",
                instructions="Create a source report",
                task_summary="Create a source report for later reuse.",
            )
        )
        reuse_task = self.engine.create_task(
            TaskSpec(
                name="Reuse report",
                instructions="Use the supplied source report",
                task_summary="Use an existing report as input.",
            )
        )

        candidates = catalog_tasks(self.db)["items"]
        selected = next(item for item in candidates if item["task_id"] == source_task["task_id"])
        self.assertEqual(get_task(self.engine, selected["task_id"])["task"]["name"], "Source report")

        source_run, _, _ = self.engine.run_task(source_task["task_id"], request=JobRequest(task="", worker="codex"))
        source_artifact = Path(source_run["artifact_path"]) / "source.md"
        source_artifact.parent.mkdir(parents=True, exist_ok=True)
        source_artifact.write_text("source report", encoding="utf-8")
        artifact_uid = new_artifact_uid()
        self.db.add_artifact(
            source_run["job_id"],
            relative_path="source.md",
            final_path=str(source_artifact),
            mime_type="text/markdown",
            size=source_artifact.stat().st_size,
            sha256=sha256_file(source_artifact),
            artifact_uid=artifact_uid,
            role="source_report",
        )
        self.db.update_job(
            source_run["job_id"],
            status="COMPLETED",
            actual_worker="codex",
            result_summary="Source report is ready for reuse.",
        )

        run_item = catalog_task_runs(self.db, status="completed")["items"][0]
        self.assertEqual(run_item["task_run_id"], source_run["job_id"])
        self.assertEqual(run_item["result_summary"], "Source report is ready for reuse.")

        reused_run, _, _ = self.engine.run_task(
            reuse_task["task_id"],
            request=JobRequest(
                task="",
                worker="codex",
                artifact_inputs=[{"artifact_uid": artifact_uid, "alias": "A1"}],
            ),
        )
        lineage = run_lineage(self.db, reused_run["job_id"])
        self.assertEqual(lineage["inputs"][0]["source_artifact_uid"], artifact_uid)
        self.assertEqual(lineage["inputs"][0]["binding_mode"], "snapshot")
        self.assertTrue((self.home / lineage["inputs"][0]["snapshot_relative_path"]).is_file())

    def test_project_catalog_is_bounded_and_run_summary_is_immutable(self):
        task = self.engine.create_task(TaskSpec(name="Project task", instructions="do project work"))
        service = ProjectService(self.db, self.engine)
        project = service.create_project(
            {
                "name": "Research pipeline",
                "description": "A bounded project description.",
                "project_summary": "Research and validate a report.",
                "nodes": [{"node_id": "source", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [{"node_id": "source", "role": "report"}],
            }
        )
        project_run = service.create_project_run(project["project_id"])

        projects = catalog_projects(self.db, limit=1)
        self.assertEqual(projects["kind"], "projects")
        self.assertEqual(projects["items"][0]["project_summary"], "Research and validate a report.")
        self.assertEqual(projects["items"][0]["node_count"], 1)
        self.assertIs(projects["items"], projects["projects"])

        definition = json.loads(project["definition_json"])
        definition["project_summary"] = "Changed current project purpose."
        service.update_project(project["project_id"], definition)
        runs = catalog_project_runs(self.db, project_id=project["project_id"], status="running")
        self.assertEqual(runs["items"][0]["project_run_id"], project_run["project_run_id"])
        self.assertEqual(runs["items"][0]["project_summary"], "Research and validate a report.")
        self.assertEqual(runs["items"][0]["status"], "running")
        self.assertEqual(runs["items"][0]["step_count"], 1)
        self.assertIs(runs["items"], runs["project_runs"])

        legacy_runs = project_runs(self.engine, project["project_id"])
        self.assertEqual(legacy_runs["kind"], "project_runs")
        self.assertIs(legacy_runs["items"], legacy_runs["project_runs"])


class CatalogCliParserTests(unittest.TestCase):
    def test_catalog_commands_parse(self):
        parser = build_parser()

        root = parser.parse_args(["catalog", "--machine"])
        tasks = parser.parse_args(["catalog", "tasks", "--limit", "2", "--updated-since", "2026-08-01"])
        runs = parser.parse_args(["catalog", "task-runs", "--status", "failed", "--task-id", "t1"])
        projects = parser.parse_args(["catalog", "projects", "--limit", "2"])
        project_runs = parser.parse_args(["catalog", "project-runs", "--status", "completed", "--project-id", "p1"])

        self.assertIsNone(root.catalog_command)
        self.assertEqual(tasks.catalog_command, "tasks")
        self.assertEqual(tasks.limit, 2)
        self.assertEqual(runs.catalog_command, "task-runs")
        self.assertEqual(runs.status, "failed")
        self.assertEqual(projects.catalog_command, "projects")
        self.assertEqual(project_runs.catalog_command, "project-runs")


class CatalogRouteTests(unittest.TestCase):
    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("daemon_port", self._free_port())
        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()
        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(5))

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except RelayError:
                pass
            self.thread.join(timeout=5)
        self.temp.cleanup()

    def test_catalog_routes_and_invalid_cursor(self):
        capability = self.client.request("GET", "/v1/catalog")
        self.assertEqual(capability["catalog_schema_version"], 1)
        tasks = self.client.request("GET", "/v1/catalog/tasks?limit=1")
        self.assertIn("tasks", tasks)
        self.assertIn("items", self.client.request("GET", "/v1/catalog/projects?limit=1"))
        self.assertIn("items", self.client.request("GET", "/v1/catalog/project-runs?limit=1"))

        with self.assertRaises(RelayError) as raised:
            self.client.request("GET", "/v1/catalog/tasks?cursor=bad")
        self.assertEqual(raised.exception.code, "INVALID_CURSOR")


if __name__ == "__main__":
    unittest.main()
