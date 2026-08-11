from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.doctor import Doctor
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import JobRequest, TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.rpc import RPCClient

ROOT = Path(__file__).resolve().parents[1]
MOCK_CODEX = ROOT / "mocks" / ("codex.cmd" if os.name == "nt" else "codex")


class AgentMissionE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.old_path = os.environ.get("PATH", "")
        self.old_test_python = os.environ.get("RELAY_TEST_PYTHON")
        os.environ["RELAY_TEST_PYTHON"] = sys.executable
        os.environ["RELAY_MISSION_E2E"] = "1"
        os.environ["PATH"] = str(ROOT / "mocks") + os.pathsep + self.old_path
        for key in list(os.environ):
            if key.startswith("RELAY_MOCK_"):
                os.environ.pop(key)
        self.config = Config(self.home)
        self.config.init()
        self.config.set("workers.codex.command", str(MOCK_CODEX))
        self.config.set("service_isolation_acknowledged", True)
        self.config.set("soft_stall_seconds", 2)
        self.config.set("hard_stall_seconds", 5)
        self.config.set("timeout_seconds", 20)
        self.config.set("poll_interval_seconds", 0.1)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        if self.old_test_python is None:
            os.environ.pop("RELAY_TEST_PYTHON", None)
        else:
            os.environ["RELAY_TEST_PYTHON"] = self.old_test_python
        os.environ.pop("RELAY_MISSION_E2E", None)
        self.temp.cleanup()

    @staticmethod
    def _free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _run_cli(self, *args):
        proc = subprocess.run(
            [sys.executable, "-m", "relay", "--home", str(self.home), *args, "--machine"],
            capture_output=True,
            text=True,
            timeout=30,
            env=os.environ.copy(),
        )
        self.assertEqual(proc.returncode, 0, f"CLI failed: {proc.stdout}\n{proc.stderr}")
        return json.loads(proc.stdout)

    def _create_tasks(self):
        definitions = [
            ("Collect source", "Collect a source report.", "Collect a source report for downstream work."),
            ("Summarize source", "Summarize supplied source report.", "Summarize an earlier source Artifact."),
            ("Validate facts", "Validate a source report.", "Validate facts and produce a validation report."),
            ("Package findings", "Package supplied validation report.", "Package an earlier validation Artifact."),
            ("Review findings", "Review final findings.", "Review findings and list open risks."),
        ]
        return [
            self.engine.create_task(
                TaskSpec(name=name, instructions=instructions, task_summary=summary, default_worker="codex")
            )
            for name, instructions, summary in definitions
        ]

    def _execute_registered_task(self, task_id, *, artifact_uid=None):
        inputs = [{"artifact_uid": artifact_uid, "alias": "A1"}] if artifact_uid else []
        job, reused, _ = self.engine.run_task(
            task_id,
            request=JobRequest(task="", worker="codex", artifact_inputs=inputs),
            queued=True,
            submitted_via="cli",
        )
        self.assertFalse(reused)
        receipt = self.engine.execute_job(job["job_id"])
        self.assertIn(receipt["status"], {"completed", "partial"})
        artifacts = self.db.artifacts_for_job(job["job_id"])
        self.assertGreaterEqual(len(artifacts), 1)
        artifact_root = self.config.path_value("artifact_root").resolve()
        stored = next(item for item in artifacts if Path(item["final_path"]).resolve().is_relative_to(artifact_root))
        return job, stored["artifact_uid"]

    def _run_project(self, service, runtime, project_id, external_inputs=None):
        payload = service.create_project_run(project_id, external_inputs=external_inputs or [])
        project_run_id = payload["project_run_id"]
        executed: set[str] = set()
        for _ in range(12):
            runtime.tick_once()
            for step in self.db.list_project_steps(project_run_id):
                task_run_id = step.get("active_task_run_id")
                if step["status"] == "running" and task_run_id and task_run_id not in executed:
                    receipt = self.engine.execute_job(task_run_id)
                    self.assertIn(receipt["status"], {"completed", "partial"})
                    executed.add(task_run_id)
            runtime.tick_once()
            run = self.db.get_project_run(project_run_id)
            if run["status"] in {"completed", "failed", "cancelled"}:
                self.assertEqual(run["status"], "completed", f"{run}\n{self.db.list_project_steps(project_run_id)}")
                return run
        self.fail(f"Project Run did not reach terminal state: {project_run_id}")

    def test_five_tasks_two_chains_three_projects_and_cli_smoke(self):
        audit = Doctor(self.config, self.db).audit(["codex"], deep=True)
        self.assertTrue(audit["ok"], audit)
        tasks = self._create_tasks()

        first, source_uid = self._execute_registered_task(tasks[0]["task_id"])
        second, _ = self._execute_registered_task(tasks[1]["task_id"], artifact_uid=source_uid)
        third, validation_uid = self._execute_registered_task(tasks[2]["task_id"])
        fourth, _ = self._execute_registered_task(tasks[3]["task_id"], artifact_uid=validation_uid)
        fifth, _ = self._execute_registered_task(tasks[4]["task_id"])
        self.assertEqual(
            len({first["job_id"], second["job_id"], third["job_id"], fourth["job_id"], fifth["job_id"]}), 5
        )
        self.assertEqual(len(self.db.lineage_for_job(second["job_id"])), 1)
        self.assertEqual(len(self.db.lineage_for_job(fourth["job_id"])), 1)

        service = ProjectService(self.db, self.engine)
        runtime = ProjectRuntime(self.db, self.engine, service)
        sequential = service.create_project(
            {
                "name": "Sequential mission",
                "project_summary": "Run a source Task and then summarize its Artifact.",
                "nodes": [
                    {"node_id": "source", "task_id": tasks[0]["task_id"]},
                    {"node_id": "summary", "task_id": tasks[1]["task_id"]},
                ],
                "connections": [{"from_node": "source", "from_role": "output", "to_node": "summary", "to_alias": "A1"}],
                "output_selection": [{"node_id": "summary", "role": "output"}],
            }
        )
        parallel = service.create_project(
            {
                "name": "Parallel mission",
                "project_summary": "Run validation and review Tasks in parallel.",
                "nodes": [
                    {"node_id": "validate", "task_id": tasks[2]["task_id"]},
                    {"node_id": "review", "task_id": tasks[4]["task_id"]},
                ],
                "connections": [],
                "output_selection": [
                    {"node_id": "validate", "role": "output"},
                    {"node_id": "review", "role": "output"},
                ],
            }
        )
        external = service.create_project(
            {
                "name": "External input mission",
                "project_summary": "Package an externally supplied Artifact.",
                "nodes": [{"node_id": "package", "task_id": tasks[3]["task_id"]}],
                "connections": [],
                "output_selection": [{"node_id": "package", "role": "output"}],
            }
        )
        project_runs = [
            self._run_project(service, runtime, sequential["project_id"]),
            self._run_project(service, runtime, parallel["project_id"]),
            self._run_project(
                service,
                runtime,
                external["project_id"],
                [{"node_id": "package", "to_alias": "A1", "artifact_uid": source_uid}],
            ),
        ]
        self.assertEqual([run["status"] for run in project_runs], ["completed"] * 3)

        self.config.set("daemon_port", self._free_port())
        daemon = RelayDaemon(self.config)
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        client = RPCClient(self.config)
        try:
            self.assertTrue(client.wait_until_healthy(5))
            catalog = self._run_cli("catalog")
            project_catalog = self._run_cli("catalog", "projects", "--limit", "2")
            project_run_catalog = self._run_cli("catalog", "project-runs", "--status", "completed")
            self.assertEqual(catalog["catalog_schema_version"], 1)
            self.assertEqual(len(project_catalog["items"]), 2)
            self.assertTrue(project_catalog["has_more"])
            self.assertGreaterEqual(len(project_run_catalog["items"]), 3)
            self.assertTrue(all(item["status"] == "completed" for item in project_run_catalog["items"]))

            cli_run = self._run_cli("run", "--worker", "codex", "--no-fallback", "CLI smoke mission")
            self.assertEqual(cli_run["status"], "completed")
            self.assertTrue(cli_run.get("task_run_id"))
        finally:
            if thread.is_alive():
                try:
                    client.request("POST", "/shutdown")
                except RelayError:
                    pass
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
