from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

from relay.api import artifact_lineage, search_artifacts, search_runs
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.doctor import Doctor
from relay.engine import RelayEngine
from relay.models import JobRequest, TaskSpec


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_project(db: Database, project_run_id: str, timeout: int = 1200) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = db.get_project_run(project_run_id)
        if run and run["status"] in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(1)
    raise TimeoutError(f"Project Run did not finish: {project_run_id}")


def wait_for_job(engine: RelayEngine, job_id: str, timeout: int = 1200) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = engine.db.get_job(job_id)
        if job and job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return engine.receipt(job_id)
        time.sleep(1)
    raise TimeoutError(f"Task Run did not finish: {job_id}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="relay-agy-live-") as temp:
        home = Path(temp) / "relay-home"
        worker = os.environ.get("RELAY_LIVE_WORKER", "antigravity").strip().lower()
        if worker not in {"antigravity", "codex", "claude"}:
            raise ValueError(f"Unsupported live Worker: {worker}")
        config = Config(home)
        config.init()
        config.set("service_isolation_acknowledged", True)
        config.set("daemon_port", free_port())
        config.set("max_concurrent_jobs", 3)
        config.set("timeout_seconds", 900)
        config.set("soft_stall_seconds", 600)
        config.set("hard_stall_seconds", 900)
        config.set("poll_interval_seconds", 0.5)
        config.set(f"workers.{worker}.enabled", True)
        if worker == "antigravity":
            config.set("workers.antigravity.security_verified", True)
            config.set("workers.antigravity.full_access_mode", True)
            config.set("workers.antigravity.default_model", "gemini-3.6-flash-high")

        db = Database(config.path_value("database_path"))
        engine = RelayEngine(config, db)
        audit = None
        for _ in range(3):
            audit = Doctor(config, db).audit([worker], deep=True)
            if audit["ok"]:
                break
            time.sleep(2)
        assert audit is not None
        if not audit["ok"]:
            raise RuntimeError(json.dumps(audit, ensure_ascii=False))

        daemon = RelayDaemon(config)
        thread = threading.Thread(target=daemon.serve, name="live-agy-daemon", daemon=True)
        thread.start()
        try:
            tasks: dict[str, dict] = {}
            brief = (
                "Relay is a local broker where an Agent registers reusable Tasks, executes them through a Worker, "
                "stores immutable Artifacts, and composes Projects as dependency graphs. The purpose of this run "
                "is to evaluate whether the current Agent Catalog and Project model are ready for orchestration."
            )
            definitions = {
                "evidence": ("Evidence analysis", f"{brief} Analyze the evidence and list the strongest facts."),
                "architecture": (
                    "Architecture analysis",
                    f"{brief} Analyze the architecture and identify the most important reusable boundaries.",
                ),
                "risk": ("Risk analysis", f"{brief} Perform an adversarial risk review and list concrete risks."),
                "synthesis": (
                    "Decision synthesis",
                    f"{brief} Read input Artifacts A1, A2, and A3. Synthesize a decision package with evidence, "
                    "architecture implications, risks, and a recommendation.",
                ),
                "review": (
                    "Final decision review",
                    "Read input Artifact A1, review the decision package, and produce a concise final review with "
                    "approval conditions and unresolved questions.",
                ),
            }
            for key, (name, instructions) in definitions.items():
                tasks[key] = engine.create_task(
                    TaskSpec(
                        name=name,
                        instructions=(
                            instructions
                            + " Do not edit the repository or external files. Return valid JSON with schema_version, "
                            "status, answer, sources, uncertainties, missing_items, and artifacts. Create exactly one "
                            f"UTF-8 Markdown artifact named {key}-memo.md in the Relay artifacts directory and list it "
                            "in the artifacts array."
                        ),
                        task_summary=instructions,
                        default_worker=worker,
                        fallback_enabled=False,
                        profile="analysis-only",
                        result_format="json",
                    )
                )

            service = daemon.project_service
            project = service.create_project(
                {
                    "name": "Live Agent Catalog Decision Package",
                    "project_summary": "Three independent live analyses converge into a decision package.",
                    "nodes": [
                        {"node_id": "evidence", "task_id": tasks["evidence"]["task_id"]},
                        {"node_id": "architecture", "task_id": tasks["architecture"]["task_id"]},
                        {"node_id": "risk", "task_id": tasks["risk"]["task_id"]},
                        {"node_id": "synthesis", "task_id": tasks["synthesis"]["task_id"]},
                    ],
                    "connections": [
                        {"from_node": "evidence", "from_role": "output", "to_node": "synthesis", "to_alias": "A1"},
                        {
                            "from_node": "architecture",
                            "from_role": "output",
                            "to_node": "synthesis",
                            "to_alias": "A2",
                        },
                        {"from_node": "risk", "from_role": "output", "to_node": "synthesis", "to_alias": "A3"},
                    ],
                    "output_selection": [{"node_id": "synthesis", "role": "output"}],
                }
            )
            created_run = service.create_project_run(project["project_id"])
            project_run = wait_for_project(db, created_run["project_run_id"])
            steps = db.list_project_steps(created_run["project_run_id"])
            if project_run["status"] != "completed":
                raise RuntimeError(json.dumps({"project_run": project_run, "steps": steps}, ensure_ascii=False))

            synthesis_step = next(step for step in steps if step["node_id"] == "synthesis")
            output_artifacts = [
                item
                for item in db.artifacts_for_job(synthesis_step["active_task_run_id"])
                if item.get("role") == "output"
            ]
            if len(output_artifacts) != 1:
                raise RuntimeError(f"Expected one synthesis output Artifact, got {output_artifacts}")
            synthesis_artifact = output_artifacts[0]

            job, reused, _ = engine.run_task(
                tasks["review"]["task_id"],
                request=JobRequest(
                    task="",
                    worker=worker,
                    fallback=False,
                    artifact_inputs=[{"artifact_uid": synthesis_artifact["artifact_uid"], "alias": "A1"}],
                ),
                queued=True,
                submitted_via="cli",
            )
            if reused:
                raise RuntimeError(f"Unexpected reused review Task Run: {job['job_id']}")
            review_receipt = wait_for_job(engine, job["job_id"])
            review_lineage = db.lineage_for_job(job["job_id"])
            source_lineage = artifact_lineage(db, synthesis_artifact["artifact_uid"])
            run_search = search_runs(db, query="Decision", limit=20)
            artifact_search = search_artifacts(db, query="decision", limit=20)

            print(
                json.dumps(
                    {
                        "worker": worker,
                        "worker_version": audit["workers"][0]["version"],
                        "full_access_mode": True,
                        "doctor": audit,
                        "task_count": len(tasks),
                        "project": {
                            "project_id": project["project_id"],
                            "project_run_id": project_run["project_run_id"],
                            "status": project_run["status"],
                            "step_statuses": {step["node_id"]: step["status"] for step in steps},
                        },
                        "synthesis_artifact_uid": synthesis_artifact["artifact_uid"],
                        "review_task_run_id": job["job_id"],
                        "review_receipt": {
                            "status": review_receipt.get("status"),
                            "result_summary": review_receipt.get("result_summary"),
                            "failure_reason": review_receipt.get("failure_reason"),
                            "error_code": review_receipt.get("error_code"),
                            "error_message": review_receipt.get("error_message"),
                            "attempts": review_receipt.get("attempts"),
                        },
                        "review_lineage_count": len(review_lineage),
                        "synthesis_artifact_consumer_count": len(source_lineage["consumers"]),
                        "search": {
                            "run_results": len(run_search["items"]),
                            "artifact_results": len(artifact_search["items"]),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            if thread.is_alive():
                daemon.server.shutdown()
                thread.join(timeout=15)
                daemon.server.server_close()


if __name__ == "__main__":
    main()
