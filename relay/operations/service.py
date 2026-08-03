from __future__ import annotations

import json
from typing import Any

from ..db import Database


class OperationsDashboardService:
    def __init__(self, db: Database):
        self.db = db

    def routine_dashboard(self, limit: int = 50) -> list[dict[str, Any]]:
        routines = self.db.list_routines(limit=limit)
        results = []
        for r in routines:
            rid = r["routine_id"]
            runs = self.db.list_routine_runs(routine_id=rid, limit=100)
            total = len(runs)
            completed = sum(1 for run in runs if run["status"] == "completed")
            failed = sum(1 for run in runs if run["status"] == "failed")

            success_rate = (completed / total * 100.0) if total > 0 else 100.0

            last_run = runs[0] if runs else None
            results.append({
                "routine_id": rid,
                "name": r["name"],
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "enabled": bool(r.get("enabled", 1)),
                "total_runs": total,
                "completed_runs": completed,
                "failed_runs": failed,
                "success_rate_percent": round(success_rate, 1),
                "last_run_status": last_run["status"] if last_run else None,
                "last_run_at": last_run["created_at"] if last_run else None,
                "next_run_at_utc": r.get("next_run_at_utc"),
            })
        return results

    def project_dashboard(self, limit: int = 50) -> list[dict[str, Any]]:
        projects = self.db.list_projects(limit=limit)
        results = []
        for p in projects:
            pid = p["project_id"]
            runs = self.db.list_project_runs(project_id=pid, limit=100)
            total = len(runs)
            completed = sum(1 for run in runs if run["status"] == "completed")
            failed = sum(1 for run in runs if run["status"] == "failed")

            success_rate = (completed / total * 100.0) if total > 0 else 100.0

            last_run = runs[0] if runs else None
            results.append({
                "project_id": pid,
                "name": p["name"],
                "version": p["version"],
                "total_runs": total,
                "completed_runs": completed,
                "failed_runs": failed,
                "success_rate_percent": round(success_rate, 1),
                "last_run_status": last_run["status"] if last_run else None,
                "last_run_at": last_run["created_at"] if last_run else None,
            })
        return results
