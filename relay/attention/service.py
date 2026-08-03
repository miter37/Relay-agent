from __future__ import annotations

from typing import Any

from ..db import Database
from ..quality.service import QualityService


class AttentionService:
    def __init__(self, db: Database):
        self.db = db
        self.quality_service = QualityService(db)

    def list_items(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        # 1. Failed Jobs
        if kind in {None, "failed_job", "failed"}:
            failed_jobs = self.db.list_jobs_page(bucket="finished", status="FAILED", limit=limit)
            for j in failed_jobs:
                items.append(
                    {
                        "item_id": f"job-{j['job_id']}",
                        "kind": "failed_job",
                        "title": j.get("title") or j["job_id"],
                        "reference_id": j["job_id"],
                        "reason": j.get("error_message") or "Job failed",
                        "created_at": j.get("completed_at") or j.get("created_at"),
                    }
                )

            failed_projects = self.db.list_project_runs(status="failed", limit=limit)
            for run in failed_projects:
                project = self.db.get_project(run["project_id"])
                items.append(
                    {
                        "item_id": f"project-{run['project_run_id']}",
                        "kind": "failed_project",
                        "title": project.get("name") if project else run["project_run_id"],
                        "reference_id": run["project_run_id"],
                        "reason": "Project Run failed",
                        "created_at": run.get("completed_at") or run.get("created_at"),
                    }
                )

        # 2. Checkpoint Approvals
        if kind in {None, "approval"}:
            with self.db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM approvals WHERE status='pending' ORDER BY created_at LIMIT ?", (limit,)
                ).fetchall()
                for r in rows:
                    items.append(
                        {
                            "item_id": f"app-{r['approval_id']}",
                            "kind": "approval",
                            "title": f"Checkpoint approval for {r['node_id']}",
                            "reference_id": r["project_run_id"],
                            "token": r["token"],
                            "reason": f"Awaiting human approval at node {r['node_id']}",
                            "created_at": r["created_at"],
                        }
                    )

        # 3. Low quality runs
        if kind in {None, "low_quality"}:
            low_q = self.quality_service.attention_runs(status_filter="low", limit=limit)
            for q in low_q:
                # avoid duplicates if already listed under failed_job
                if not any(i["reference_id"] == q["run_id"] for i in items):
                    items.append(
                        {
                            "item_id": f"quality-{q['run_id']}",
                            "kind": "low_quality",
                            "title": q["title"],
                            "reference_id": q["run_id"],
                            "reason": q["reason"],
                            "created_at": q["created_at"],
                        }
                    )

        items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return items[:limit]
