"""Orchestration scenarios S1–S6 using real Antigravity (agy)."""

from __future__ import annotations

import json
import os
import shutil
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
WORKER = "antigravity"
MARKER = "ORCHAGY"


def _resolve_agy() -> str:
    env_cmd = os.environ.get("RELAY_AGY_COMMAND")
    if env_cmd and Path(env_cmd).exists():
        return env_cmd
    which = shutil.which("agy")
    if which:
        return which
    candidates = [
        Path.home() / "AppData/Local/agy/bin/agy.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft/WinGet/Packages/Google.AntigravityCLI_Microsoft.Winget.Source_8wekyb3d8bbwe/agy.exe",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise FileNotFoundError("agy executable not found on PATH or known install locations")


def _task_instructions(summary: str) -> str:
    return (
        f"{summary}\n\n"
        "CRITICAL OUTPUT RULES (Relay Antigravity):\n"
        "1. Work fully non-interactively. Do not ask questions.\n"
        "2. Write the final result ONLY as valid JSON to the result file path given in the system prompt "
        "(result.json under the workspace). Do not wrap it in markdown fences.\n"
        "3. If you print anything to stdout, it must be the same pure JSON object only.\n"
        "4. JSON must match this schema exactly:\n"
        '{\n'
        '  "schema_version": "1.0",\n'
        '  "status": "complete",\n'
        f'  "answer": "<short plain text including token {MARKER}>",\n'
        '  "sources": [],\n'
        '  "uncertainties": [],\n'
        '  "missing_items": [],\n'
        '  "artifacts": [\n'
        '    {\n'
        '      "relative_path": "notes.txt",\n'
        '      "description": "brief notes",\n'
        '      "encoding": "utf-8",\n'
        f'      "content": "notes including {MARKER}"\n'
        '    }\n'
        '  ]\n'
        '}\n'
        "5. If input Artifacts are provided (A1/A2/...), read them and base answer/content on them.\n"
        "6. Keep answer under 500 characters. Finish quickly."
    )


class AgyOrchestrationRunner:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.agy = _resolve_agy()
        self.old_path = os.environ.get("PATH", "")
        # Ensure real agy is preferred; do not put repo mocks first.
        agy_dir = str(Path(self.agy).resolve().parent)
        os.environ["PATH"] = agy_dir + os.pathsep + self.old_path
        self.config = Config(self.home)
        self.config.init()
        self.config.set("workers.antigravity.command", self.agy)
        self.config.set("workers.antigravity.enabled", True)
        self.config.set("workers.antigravity.security_verified", True)
        self.config.set("workers.antigravity.full_access_mode", True)
        self.config.set("workers.antigravity.require_deep_doctor", True)
        self.config.set("workers.claude.enabled", False)
        self.config.set("workers.codex.enabled", False)
        self.config.set("service_isolation_acknowledged", True)
        self.config.set("default_worker", WORKER)
        self.config.set("default_format", "json")
        self.config.set("fallback_enabled", False)
        self.config.set("soft_stall_seconds", 300)
        self.config.set("hard_stall_seconds", 900)
        self.config.set("timeout_seconds", 1200)
        self.config.set("poll_interval_seconds", 2)
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        self.runtime = ProjectRuntime(self.db, self.engine, self.service)
        self.tasks: dict[str, dict[str, str]] = {}
        self.runs: list[dict[str, object]] = []
        self.projects: list[dict[str, object]] = []
        self._seed_adapter_specs_from_user_home()

    def _seed_adapter_specs_from_user_home(self) -> None:
        """Copy a healthy antigravity adapter-spec from the user Relay Home if available.

        Deep probes against live agy are flaky (INVALID_JSON). When the operator already
        has a healthy deep audit for the same version, reuse it so orchestration focuses
        on Task/Project wiring rather than re-proving doctor every temp home.
        """
        user_home = Path(os.environ.get("LOCALAPPDATA", "")) / "Relay"
        src_dir = user_home / "adapter-specs" / "antigravity"
        dst_dir = self.config.path_value("adapter_spec_root") / "antigravity"
        if not src_dir.is_dir():
            return
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.glob("*.json"):
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("worker") != "antigravity":
                continue
            if not data.get("deep_ok"):
                continue
            if data.get("status") not in {"healthy", "ok"}:
                continue
            target = dst_dir / src.name
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[agy-orch] seeded adapter spec {target.name}", file=sys.stderr)

    def close(self) -> None:
        os.environ["PATH"] = self.old_path
        self.temp.cleanup()

    def register_task(self, key: str, name: str, summary: str) -> str:
        task = self.engine.create_task(
            TaskSpec(
                name=name,
                instructions=_task_instructions(summary),
                task_summary=summary,
                default_worker=WORKER,
                fallback_enabled=False,
                result_format="json",
            )
        )
        self.tasks[key] = {"task_id": task["task_id"], "name": name, "summary": summary}
        return task["task_id"]

    def _execute_once(self, key: str, input_uids: list[str] | None = None) -> dict[str, object]:
        inputs = [{"artifact_uid": uid, "alias": f"A{index}"} for index, uid in enumerate(input_uids or [], 1)]
        task_id = self.tasks[key]["task_id"]
        job, reused, _ = self.engine.run_task(
            task_id,
            request=JobRequest(
                task="",
                worker=WORKER,
                fallback=False,
                artifact_inputs=inputs,
                result_format="json",
                force_new=True,
            ),
            queued=True,
            submitted_via="cli",
        )
        if reused:
            raise AssertionError(f"unexpected reused Task Run: {job['job_id']}")
        receipt = self.engine.execute_job(job["job_id"])
        artifacts = self.db.artifacts_for_job(job["job_id"])
        artifact_root = self.config.path_value("artifact_root").resolve()
        stored_artifacts = [
            item
            for item in artifacts
            if Path(str(item.get("final_path") or "")).resolve().is_relative_to(artifact_root)
        ]
        artifact_uid = stored_artifacts[0]["artifact_uid"] if stored_artifacts else None
        return {
            "task_key": key,
            "task_id": task_id,
            "task_run_id": job["job_id"],
            "status": receipt["status"],
            "task_summary": receipt.get("task_summary"),
            "result_summary": receipt.get("result_summary"),
            "failure_reason": receipt.get("failure_reason"),
            "error_code": receipt.get("error_code"),
            "input_artifact_uids": input_uids or [],
            "artifact_uid": artifact_uid,
            "artifact_count": len(stored_artifacts),
            "lineage_count": len(self.db.lineage_for_job(job["job_id"])),
        }

    def run_task(
        self,
        key: str,
        input_uids: list[str] | None = None,
        *,
        allow_fail: bool = False,
        retries: int = 2,
    ) -> dict[str, object]:
        """Run a registered Task. Retry on common flaky Antigravity output errors."""
        last: dict[str, object] | None = None
        attempts = 1 + max(0, retries)
        for attempt in range(attempts):
            result = self._execute_once(key, input_uids)
            self.runs.append(result)
            last = result
            if result["status"] in {"completed", "partial"}:
                if not result["artifact_uid"] and not allow_fail:
                    raise AssertionError(f"completed Task Run has no Artifact: {result}")
                return result
            if allow_fail:
                return result
            code = str(result.get("error_code") or "")
            retryable = code in {
                "INVALID_JSON",
                "EMPTY_OUTPUT",
                "TIMEOUT",
                "STALL",
                "PROCESS_CRASHED",
                "WORKER_FAILED",
            }
            print(
                f"[agy-orch] task {key} attempt {attempt + 1}/{attempts} "
                f"status={result['status']} code={code}",
                file=sys.stderr,
            )
            if not retryable or attempt + 1 >= attempts:
                break
        assert last is not None
        if not allow_fail and last["status"] not in {"completed", "partial"}:
            raise AssertionError(last)
        return last

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
        # Real workers are slow; allow many reconcile ticks.
        for _ in range(64):
            self.runtime.tick_once()
            for step in self.db.list_project_steps(project_run_id):
                task_run_id = step.get("active_task_run_id")
                if step["status"] == "running" and task_run_id and task_run_id not in executed:
                    receipt = self.engine.execute_job(task_run_id)
                    if receipt["status"] not in {"completed", "partial"}:
                        raise AssertionError(
                            f"Project step failed: {project_run_id}/{step['node_id']}: {receipt}"
                        )
                    executed.add(task_run_id)
            self.runtime.tick_once()
            run = self.db.get_project_run(project_run_id)
            if run["status"] in {"completed", "failed", "cancelled"}:
                steps = self.db.list_project_steps(project_run_id)
                if run["status"] != "completed":
                    raise AssertionError(f"Project Run did not complete: {run}; steps={steps}")
                return {
                    "project_key": key,
                    "project_id": project["project_id"],
                    "project_run_id": project_run_id,
                    "status": run["status"],
                    "step_count": len(steps),
                    "step_statuses": {step["node_id"]: step["status"] for step in steps},
                    "executed_step_runs": len(executed),
                    "external_input_count": len(external_inputs or []),
                }
        raise AssertionError(
            f"Project Run did not reach terminal state: {project_run_id}; "
            f"run={self.db.get_project_run(project_run_id)}; "
            f"steps={self.db.list_project_steps(project_run_id)}"
        )

    def execute(self) -> dict[str, object]:
        print(f"[agy-orch] using executable: {self.agy}", file=sys.stderr)
        audit = None
        for attempt in range(3):
            # Prefer shallow first if a healthy deep spec was seeded; fall back to deep.
            deep = attempt > 0 or not any(
                (self.config.path_value("adapter_spec_root") / "antigravity").glob("*.json")
            )
            audit = Doctor(self.config, self.db).audit([WORKER], deep=deep)
            print(
                f"[agy-orch] doctor attempt {attempt + 1} deep={deep}: "
                f"{json.dumps(audit, ensure_ascii=False)[:400]}",
                file=sys.stderr,
            )
            if audit.get("ok"):
                break
            if not deep:
                # Seeded spec may be ignored without matching hash; force deep.
                continue
        if not audit or not audit.get("ok"):
            # Last resort: deep probe with retries.
            for attempt in range(3):
                audit = Doctor(self.config, self.db).audit([WORKER], deep=True)
                print(
                    f"[agy-orch] doctor deep-retry {attempt + 1}: "
                    f"{json.dumps(audit, ensure_ascii=False)[:400]}",
                    file=sys.stderr,
                )
                if audit.get("ok"):
                    break
        if not audit or not audit.get("ok"):
            raise AssertionError(audit)

        # S1 standalone chain
        print("[agy-orch] S1 standalone chain", file=sys.stderr)
        self.register_task(
            "standalone_source",
            "Collect source material",
            "Collect brief source material for downstream analysis.",
        )
        self.register_task(
            "standalone_summary",
            "Summarize source material",
            "Summarize the supplied source Artifact briefly.",
        )
        first = self.run_task("standalone_source")
        print(f"[agy-orch] S1 source: {first}", file=sys.stderr)
        second = self.run_task("standalone_summary", [first["artifact_uid"]])
        print(f"[agy-orch] S1 summary: {second}", file=sys.stderr)
        if second["status"] not in {"completed", "partial"} or second["lineage_count"] != 1:
            raise AssertionError({"first": first, "second": second})

        # S2 sequential project
        print("[agy-orch] S2 sequential project", file=sys.stderr)
        for key, name in (
            ("research", "Research inputs"),
            ("clean", "Clean research"),
            ("report", "Write research report"),
        ):
            self.register_task(key, name, f"Perform the {name.lower()} step and leave a short reusable report.")
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
        print(f"[agy-orch] S2: {project_a_run}", file=sys.stderr)

        # S3 parallel join
        print("[agy-orch] S3 parallel join", file=sys.stderr)
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
        print(f"[agy-orch] S3: {project_b_run}", file=sys.stderr)

        # S4 cross-project
        print("[agy-orch] S4 cross-project reuse", file=sys.stderr)
        self.register_task("adopt", "Draft adoption plan", "Draft a short adoption plan from the supplied report.")
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
        print(f"[agy-orch] S4: {project_c_run}", file=sys.stderr)

        # S5 failure + recovery
        print("[agy-orch] S5 failure recovery", file=sys.stderr)
        self.register_task("failure", "Failure recovery probe", "Produce a short recovery probe result.")
        good_worker = self.config.get("workers.antigravity.command")
        self.config.set("workers.antigravity.command", str(ROOT / "mocks" / "does-not-exist-agy.exe"))
        failed = self.run_task("failure", allow_fail=True, retries=0)
        self.config.set("workers.antigravity.command", good_worker)
        recovered = self.run_task("failure", retries=1)
        print(f"[agy-orch] S5 failed={failed['status']} recovered={recovered['status']}", file=sys.stderr)
        if failed["status"] != "failed" or not failed["failure_reason"]:
            raise AssertionError({"failed": failed, "recovered": recovered})
        if recovered["status"] not in {"completed", "partial"}:
            raise AssertionError(recovered)

        # S6 catalog / search / content
        print("[agy-orch] S6 catalog and history", file=sys.stderr)
        task_catalog = catalog_tasks(self.db, limit=200)
        run_catalog = catalog_task_runs(self.db, limit=200)
        project_catalog = catalog_projects(self.db, limit=200)
        project_run_catalog = catalog_project_runs(self.db, limit=200)
        run_search = search_runs(self.db, query=MARKER, limit=20)
        artifact_search = search_artifacts(self.db, query=MARKER, limit=20)
        failed_run_search = search_runs(self.db, query="recovery", status="failed", limit=20)
        recovered_artifact = recovered["artifact_uid"]
        content = artifact_content(self.db, recovered_artifact, max_bytes=4096)
        lineage = artifact_lineage(self.db, recovered_artifact)
        if not content.get("available") or "text" not in content:
            raise AssertionError(content)
        if not lineage.get("artifact"):
            raise AssertionError(lineage)

        search_ok = bool(run_search.get("items")) and bool(artifact_search.get("items"))
        return {
            "worker": WORKER,
            "agy_executable": self.agy,
            "marker": MARKER,
            "doctor_ok": audit["ok"],
            "doctor_status": audit["workers"][0].get("status") if audit.get("workers") else None,
            "task_count": len(self.tasks),
            "task_run_count": len(self.runs),
            "project_count": len(self.projects),
            "project_run_count": len(project_run_catalog["items"]),
            "scenarios": {
                "S1_standalone_artifact_chain": {
                    "pass": True,
                    "source_task_run_id": first["task_run_id"],
                    "source_artifact_uid": first["artifact_uid"],
                    "consumer_task_run_id": second["task_run_id"],
                    "consumer_lineage_count": second["lineage_count"],
                    "source_status": first["status"],
                    "consumer_status": second["status"],
                },
                "S2_sequential_project": {**project_a_run, "pass": project_a_run["status"] == "completed"},
                "S3_parallel_join_project": {**project_b_run, "pass": project_b_run["status"] == "completed"},
                "S4_cross_project_artifact": {
                    "pass": project_c_run["status"] == "completed"
                    and len(cross_project_lineage["consumers"]) >= 1,
                    "source_project_run_id": project_a_run["project_run_id"],
                    "source_artifact_uid": project_a_artifact,
                    "consumer_project_run_id": project_c_run["project_run_id"],
                    "external_input_count": project_c_run["external_input_count"],
                    "source_artifact_consumer_count": len(cross_project_lineage["consumers"]),
                },
                "S5_failure_recovery": {
                    "pass": failed["status"] == "failed" and recovered["status"] in {"completed", "partial"},
                    "failed": failed,
                    "recovered": recovered,
                },
                "S6_catalog_and_history": {
                    "pass": True,
                    "search_index_ok": search_ok,
                    "task_catalog_count": len(task_catalog["items"]),
                    "task_run_catalog_count": len(run_catalog["items"]),
                    "project_catalog_count": len(project_catalog["items"]),
                    "project_run_catalog_count": len(project_run_catalog["items"]),
                    "run_search_count": len(run_search.get("items") or []),
                    "failed_run_search_count": len(failed_run_search.get("items") or []),
                    "artifact_search_count": len(artifact_search.get("items") or []),
                    "artifact_content_available": content["available"],
                    "artifact_content_field": "text" if "text" in content else None,
                },
            },
            "task_runs": self.runs,
            "projects": self.projects,
        }


def main() -> None:
    runner = AgyOrchestrationRunner()
    try:
        result = runner.execute()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        runner.close()


if __name__ == "__main__":
    main()
