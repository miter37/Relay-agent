from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from relay.api import (
    artifact_content,
    artifact_lineage,
    catalog_project_runs,
    catalog_projects,
    catalog_task_runs,
    catalog_tasks,
    search_artifacts,
    search_runs,
)
from relay.config import Config
from relay.db import Database
from relay.doctor import Doctor
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService

ROOT = Path(__file__).resolve().parents[1]
MOCK_CODEX = ROOT / "mocks" / ("codex.cmd" if os.name == "nt" else "codex")


class OrchestrationScenarioRunner:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.old_path = os.environ.get("PATH", "")
        self.old_test_python = os.environ.get("RELAY_TEST_PYTHON")
        self.config = Config(self.home)
        self.config.init()
        self.config.set("workers.codex.command", str(MOCK_CODEX))
        self.config.set("service_isolation_acknowledged", True)
        self.config.set("soft_stall_seconds", 2)
        self.config.set("hard_stall_seconds", 5)
        self.config.set("timeout_seconds", 20)
        self.config.set("poll_interval_seconds", 0.1)
        os.environ["RELAY_TEST_PYTHON"] = sys.executable
        os.environ["RELAY_MISSION_E2E"] = "1"
        os.environ["PATH"] = str(ROOT / "mocks") + os.pathsep + self.old_path
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)
        self.tasks: dict[str, dict[str, str]] = {}
        self.runs: list[dict[str, object]] = []
        self.projects: list[dict[str, object]] = []

    def close(self) -> None:
        os.environ["PATH"] = self.old_path
        if self.old_test_python is None:
            os.environ.pop("RELAY_TEST_PYTHON", None)
        else:
            os.environ["RELAY_TEST_PYTHON"] = self.old_test_python
        os.environ.pop("RELAY_MISSION_E2E", None)
        self.temp.cleanup()

    def register_task(self, key: str, name: str, summary: str) -> str:
        task = self.engine.create_task(
            TaskSpec(
                name=name,
                instructions=summary,
                task_summary=summary,
                default_worker="codex",
                fallback_enabled=False,
            )
        )
        self.tasks[key] = {"task_id": task["task_id"], "name": name, "summary": summary}
        return task["task_id"]

    def run_task(self, key: str, input_uids: list[str] | None = None) -> dict[str, object]:
        inputs = [{"artifact_uid": uid, "alias": f"A{index}"} for index, uid in enumerate(input_uids or [], 1)]
        task_id = self.tasks[key]["task_id"]
        job, reused, _ = self.engine.run_task(
            task_id,
            request=JobRequest(task="", worker="codex", fallback=False, artifact_inputs=inputs),
            queued=True,
            submitted_via="cli",
        )
        if reused:
            raise AssertionError(f"unexpected reused Task Run: {job['job_id']}")
        receipt = self.engine.execute_job(job["job_id"])
        artifacts = self.db.artifacts_for_job(job["job_id"])
        if receipt["status"] in {"completed", "partial"} and not artifacts:
            raise AssertionError(f"completed Task Run has no Artifact: {job['job_id']}")
        artifact_root = self.config.path_value("artifact_root").resolve()
        stored_artifacts = [
            item
            for item in artifacts
            if Path(str(item.get("final_path") or "")).resolve().is_relative_to(artifact_root)
        ]
        artifact_uid = stored_artifacts[0]["artifact_uid"] if stored_artifacts else None
        result = {
            "task_key": key,
            "task_id": task_id,
            "task_run_id": job["job_id"],
            "status": receipt["status"],
            "task_summary": receipt.get("task_summary"),
            "result_summary": receipt.get("result_summary"),
            "failure_reason": receipt.get("failure_reason"),
            "input_artifact_uids": input_uids or [],
            "artifact_uid": artifact_uid,
            "artifact_count": len(stored_artifacts),
            "lineage_count": len(self.db.lineage_for_job(job["job_id"])),
        }
        self.runs.append(result)
        return result

    def create_project(self, key: str, name: str, summary: str, nodes: list[dict], connections: list[dict], output):
        project = self.service.create_project(
            {
                "name": name,
                "project_summary": summary,
                "nodes": nodes,
                "connections": connections,
                "output_selection": output,
            }
        )
        self.projects.append({"key": key, "project_id": project["project_id"], "name": name, "summary": summary})
        return project

    def run_project(self, key: str, project: dict, external_inputs: list[dict] | None = None) -> dict[str, object]:
        created = self.service.create_project_run(project["project_id"], external_inputs=external_inputs or [])
        project_run_id = created["project_run_id"]
        executed: set[str] = set()
        for _ in range(24):
            self.runtime.tick_once()
            for step in self.db.list_project_steps(project_run_id):
                task_run_id = step.get("active_task_run_id")
                if step["status"] == "running" and task_run_id and task_run_id not in executed:
                    receipt = self.engine.execute_job(task_run_id)
                    if receipt["status"] not in {"completed", "partial"}:
                        raise AssertionError(f"Project step failed: {project_run_id}/{step['node_id']}: {receipt}")
                    executed.add(task_run_id)
            self.runtime.tick_once()
            run = self.db.get_project_run(project_run_id)
            if run["status"] in {"completed", "failed", "cancelled"}:
                steps = self.db.list_project_steps(project_run_id)
                if run["status"] != "completed":
                    raise AssertionError(f"Project Run did not complete: {run}")
                result = {
                    "project_key": key,
                    "project_id": project["project_id"],
                    "project_run_id": project_run_id,
                    "status": run["status"],
                    "step_count": len(steps),
                    "step_statuses": {step["node_id"]: step["status"] for step in steps},
                    "executed_step_runs": len(executed),
                    "external_input_count": len(external_inputs or []),
                }
                return result
        raise AssertionError(
            f"Project Run did not reach terminal state: {project_run_id}; "
            f"run={self.db.get_project_run(project_run_id)}; "
            f"steps={self.db.list_project_steps(project_run_id)}"
        )

    def execute(self) -> dict[str, object]:
        audit = Doctor(self.config, self.db).audit(["codex"], deep=True)
        if not audit["ok"]:
            raise AssertionError(audit)

        # Scenario 1: two standalone Tasks, with the first Artifact supplied to the second.
        self.register_task(
            "standalone_source", "Collect source material", "Collect source material for downstream analysis."
        )
        self.register_task("standalone_summary", "Summarize source material", "Summarize the supplied source Artifact.")
        first = self.run_task("standalone_source")
        second = self.run_task("standalone_summary", [first["artifact_uid"]])
        if second["lineage_count"] != 1:
            raise AssertionError(second)

        # Project 1: sequential source -> clean -> report.
        for key, name in (
            ("research", "Research inputs"),
            ("clean", "Clean research"),
            ("report", "Write research report"),
        ):
            self.register_task(key, name, f"Perform the {name.lower()} step and leave a reusable report.")
        project_a = self.create_project(
            "sequential",
            "Market research report",
            "Collect, clean, and report market research in sequence.",
            [
                {"node_id": "research", "task_id": self.tasks["research"]["task_id"]},
                {"node_id": "clean", "task_id": self.tasks["clean"]["task_id"]},
                {"node_id": "report", "task_id": self.tasks["report"]["task_id"]},
            ],
            [
                {"from_node": "research", "from_role": "output", "to_node": "clean", "to_alias": "A1"},
                {"from_node": "clean", "from_role": "output", "to_node": "report", "to_alias": "A1"},
            ],
            [{"node_id": "report", "role": "output"}],
        )
        project_a_run = self.run_project("sequential", project_a)

        # Scenario 2: two independent branches feed one synthesis Task.
        for key, name in (
            ("market", "Analyze market potential"),
            ("risk", "Analyze delivery risk"),
            ("synthesis", "Synthesize launch decision"),
        ):
            self.register_task(key, name, f"Produce the {name.lower()} result for a launch decision.")
        project_b = self.create_project(
            "parallel_join",
            "Product launch review",
            "Analyze market and risk in parallel, then synthesize a launch decision.",
            [
                {"node_id": "market", "task_id": self.tasks["market"]["task_id"]},
                {"node_id": "risk", "task_id": self.tasks["risk"]["task_id"]},
                {"node_id": "synthesis", "task_id": self.tasks["synthesis"]["task_id"]},
            ],
            [
                {"from_node": "market", "from_role": "output", "to_node": "synthesis", "to_alias": "A1"},
                {"from_node": "risk", "from_role": "output", "to_node": "synthesis", "to_alias": "A2"},
            ],
            [{"node_id": "synthesis", "role": "output"}],
        )
        project_b_run = self.run_project("parallel_join", project_b)

        # Scenario 3: Project A's final Artifact becomes Project C's external input.
        self.register_task("adopt", "Draft adoption plan", "Draft an adoption plan from the supplied research report.")
        self.register_task("review_plan", "Review adoption plan", "Review the adoption plan and list open risks.")
        project_c = self.create_project(
            "cross_project",
            "Research-to-adoption plan",
            "Reuse a prior Project report as input to a new adoption plan.",
            [
                {"node_id": "adopt", "task_id": self.tasks["adopt"]["task_id"]},
                {"node_id": "review", "task_id": self.tasks["review_plan"]["task_id"]},
            ],
            [{"from_node": "adopt", "from_role": "output", "to_node": "review", "to_alias": "A1"}],
            [{"node_id": "review", "role": "output"}],
        )
        report_step = next(
            step for step in self.db.list_project_steps(project_a_run["project_run_id"]) if step["node_id"] == "report"
        )
        report_artifacts = self.db.artifacts_for_job(report_step["active_task_run_id"])
        artifact_root = self.config.path_value("artifact_root").resolve()
        stored_report_artifacts = [
            item
            for item in report_artifacts
            if Path(str(item.get("final_path") or "")).resolve().is_relative_to(artifact_root)
        ]
        project_a_artifact = stored_report_artifacts[0]["artifact_uid"]
        project_c_run = self.run_project(
            "cross_project",
            project_c,
            [{"node_id": "adopt", "to_alias": "A1", "artifact_uid": project_a_artifact}],
        )
        cross_project_lineage = artifact_lineage(self.db, project_a_artifact)

        # Scenario 4: a real failure receipt followed by a clean rerun.
        self.register_task("failure", "Failure recovery probe", "Produce a recovery probe result.")
        good_worker = self.config.get("workers.codex.command")
        self.config.set("workers.codex.command", str(ROOT / "mocks" / "does-not-exist.cmd"))
        failed = self.run_task("failure")
        self.config.set("workers.codex.command", good_worker)
        recovered = self.run_task("failure")
        if failed["status"] != "failed" or not failed["failure_reason"]:
            raise AssertionError({"failed": failed, "recovered": recovered})
        if recovered["status"] not in {"completed", "partial"}:
            raise AssertionError(recovered)

        # Scenario 5: catalog, historical search, content, and lineage discovery.
        task_catalog = catalog_tasks(self.db, limit=200)
        run_catalog = catalog_task_runs(self.db, limit=200)
        project_catalog = catalog_projects(self.db, limit=200)
        project_run_catalog = catalog_project_runs(self.db, limit=200)
        run_search = search_runs(self.db, query="Mock", limit=20)
        artifact_search = search_artifacts(self.db, query="RELAY_ARTIFACT_OK", limit=20)
        failed_run_search = search_runs(self.db, query="recovery", status="failed", limit=20)
        if not run_search["items"] or not artifact_search["items"]:
            raise AssertionError(f"Fresh execution was not searchable: runs={run_search}, artifacts={artifact_search}")
        if not failed_run_search["items"]:
            raise AssertionError(f"Failed execution was not searchable: {failed_run_search}")
        recovered_artifact = recovered["artifact_uid"]
        content = artifact_content(self.db, recovered_artifact, max_bytes=4096)
        lineage = artifact_lineage(self.db, recovered_artifact)
        if not content.get("available") or "text" not in content:
            raise AssertionError(content)
        if not lineage.get("artifact"):
            raise AssertionError(lineage)

        return {
            "doctor_ok": audit["ok"],
            "task_count": len(self.tasks),
            "task_run_count": len(self.runs),
            "project_count": len(self.projects),
            "project_run_count": len(project_run_catalog["items"]),
            "scenarios": {
                "standalone_artifact_chain": {
                    "source_task_run_id": first["task_run_id"],
                    "source_artifact_uid": first["artifact_uid"],
                    "consumer_task_run_id": second["task_run_id"],
                    "consumer_lineage_count": second["lineage_count"],
                },
                "sequential_project": project_a_run,
                "parallel_join_project": project_b_run,
                "cross_project_artifact": {
                    "source_project_run_id": project_a_run["project_run_id"],
                    "source_artifact_uid": project_a_artifact,
                    "consumer_project_run_id": project_c_run["project_run_id"],
                    "external_input_count": project_c_run["external_input_count"],
                    "source_artifact_consumer_count": len(cross_project_lineage["consumers"]),
                },
                "failure_recovery": {"failed": failed, "recovered": recovered},
                "catalog_and_history": {
                    "task_catalog_count": len(task_catalog["items"]),
                    "task_run_catalog_count": len(run_catalog["items"]),
                    "project_catalog_count": len(project_catalog["items"]),
                    "project_run_catalog_count": len(project_run_catalog["items"]),
                    "run_search_count": len(run_search["items"]),
                    "failed_run_search_count": len(failed_run_search["items"]),
                    "artifact_search_count": len(artifact_search["items"]),
                    "artifact_content_available": content["available"],
                    "artifact_content_field": "text" if "text" in content else None,
                    "artifact_consumer_count": len(lineage["consumers"]),
                },
            },
            "task_runs": self.runs,
            "projects": self.projects,
        }


def main() -> None:
    runner = OrchestrationScenarioRunner()
    try:
        print(json.dumps(runner.execute(), ensure_ascii=False, indent=2))
    finally:
        runner.close()


if __name__ == "__main__":
    main()
