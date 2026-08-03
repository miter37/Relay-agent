from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..db import Database
from ..errors import RelayError


class QualityService:
    def __init__(self, db: Database):
        self.db = db

    def score_run(self, run_id: str) -> dict[str, Any]:
        job = self.db.get_job(run_id)
        prun = self.db.get_project_run(run_id) if not job else None

        if not (job or prun):
            raise RelayError("JOB_NOT_FOUND", f"Run not found: {run_id}")

        if job:
            return self._score_job(job)
        else:
            return self._score_project_run(prun)  # type: ignore[arg-type]

    def _score_job(self, job: dict[str, Any]) -> dict[str, Any]:
        run_id = job["job_id"]
        status = job.get("status")
        result_status = job.get("result_status")
        error_code = job.get("error_code")

        artifacts = self.db.artifacts_for_job(run_id)
        artifact_count = len(artifacts)

        # Parse uncertainties & missing_items if output file exists
        uncertainty_count = 0
        missing_count = 0

        output_path = job.get("output_path")
        if output_path and Path(output_path).is_file():
            try:
                data = json.loads(Path(output_path).read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    uncertainty_count = len(data.get("uncertainties") or [])
                    missing_count = len(data.get("missing_items") or [])
            except Exception:
                pass

        status_ok = status == "COMPLETED" and not error_code and (result_status == "complete" or result_status is None)

        if not status_ok or status in {"FAILED", "CANCELLED"} or error_code:
            score = "low"
        elif status == "PARTIAL" or result_status == "partial":
            score = "medium"
        elif status_ok and uncertainty_count == 0 and missing_count == 0 and artifact_count > 0:
            score = "high"
        elif status_ok and uncertainty_count <= 2 and missing_count <= 2:
            score = "medium"
        else:
            score = "low"

        return {
            "run_id": run_id,
            "kind": "task_run",
            "status_ok": status_ok,
            "uncertainty_count": uncertainty_count,
            "missing_count": missing_count,
            "artifact_count": artifact_count,
            "validation_status": result_status,
            "score": score,
        }

    def _score_project_run(self, prun: dict[str, Any]) -> dict[str, Any]:
        run_id = prun["project_run_id"]
        status = prun.get("status")

        steps = self.db.list_project_steps(run_id)
        step_statuses = [s["status"] for s in steps]
        failed_steps = [s for s in steps if s["status"] == "failed"]

        status_ok = status == "completed" and len(failed_steps) == 0

        if status in {"failed", "cancelled"} or len(failed_steps) > 0:
            score = "low"
        elif status_ok and all(st == "completed" for st in step_statuses):
            score = "high"
        else:
            score = "medium"

        return {
            "run_id": run_id,
            "kind": "project_run",
            "status_ok": status_ok,
            "uncertainty_count": 0,
            "missing_count": len(failed_steps),
            "artifact_count": len(json.loads(prun.get("final_artifact_ids_json") or "[]")),
            "validation_status": status,
            "score": score,
        }

    def attention_runs(self, status_filter: str = "low", limit: int = 50) -> list[dict[str, Any]]:
        # Fetch failed/low-quality Task Runs and Project Runs.
        jobs = self.db.list_jobs_page(bucket="finished", limit=limit)
        items = []
        for job in jobs:
            sc = self._score_job(job)
            if status_filter == "all" or sc["score"] == status_filter:
                items.append(
                    {
                        "run_id": job["job_id"],
                        "kind": "task_run",
                        "title": job.get("title") or job["job_id"],
                        "status": job.get("status"),
                        "score": sc["score"],
                        "reason": job.get("error_message") or f"Quality score: {sc['score']}",
                        "created_at": job.get("created_at"),
                    }
                )
                if len(items) >= limit:
                    break
        if len(items) < limit:
            for project_run in self.db.list_project_runs(limit=limit):
                sc = self._score_project_run(project_run)
                if status_filter != "all" and sc["score"] != status_filter:
                    continue
                project = self.db.get_project(project_run["project_id"])
                items.append(
                    {
                        "run_id": project_run["project_run_id"],
                        "kind": "project_run",
                        "title": project.get("name") if project else project_run["project_run_id"],
                        "status": project_run.get("status"),
                        "score": sc["score"],
                        "reason": f"Quality score: {sc['score']}",
                        "created_at": project_run.get("completed_at") or project_run.get("created_at"),
                    }
                )
                if len(items) >= limit:
                    break
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items
