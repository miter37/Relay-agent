from __future__ import annotations

import json
import logging
import threading
from typing import Any

from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..models import JobRequest
from ..util import utc_now
from .models import ProjectSpec
from .service import ProjectService

logger = logging.getLogger(__name__)


_STEP_TERMINAL = {"completed", "failed", "cancelled", "blocked"}


class ProjectRuntime:
    def __init__(self, db: Database, engine: RelayEngine, service: ProjectService, *, tick_seconds: float = 0.5):
        self.db = db
        self.engine = engine
        self.service = service
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="project-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def wake(self) -> None:
        self._wake.set()

    def tick_once(self) -> None:
        try:
            self._reconcile_all_runs()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("project runtime tick failed: %s", exc)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._reconcile_all_runs()
            except Exception as exc:  # pragma: no cover
                logger.exception("project runtime loop error: %s", exc)
            self._wake.wait(self.tick_seconds)
            self._wake.clear()

    # --- reconciliation --------------------------------------------------------

    def _reconcile_all_runs(self) -> None:
        running = self.db.list_project_runs(status="running", limit=200)
        for run in running:
            try:
                self._reconcile_one(run)
            except Exception as exc:  # pragma: no cover - reconcile is defensive
                logger.exception("project run reconcile error: %s", exc)

    def _reconcile_one(self, run: dict[str, Any]) -> None:
        project_run_id = run["project_run_id"]
        snapshot = json.loads(run["project_snapshot_json"])
        spec = ProjectSpec.from_dict(snapshot["project_definition"])
        steps = self.db.list_project_steps(project_run_id)
        step_by_id = {s["node_id"]: s for s in steps}

        # 1. Reconcile queued/running steps against Task Run status.
        for step in steps:
            if step["status"] not in {"queued", "running"}:
                continue
            task_run_id = step.get("active_task_run_id")
            if not task_run_id:
                continue
            job = self.engine.db.get_job(task_run_id)
            if not job:
                continue
            job_status = job.get("status")
            if job_status in {"COMPLETED"}:
                # Skip if step already processed (prevents duplicate checkpoint pausing on restart)
                if step["status"] in {"awaiting_approval", "completed"}:
                    continue

                artifacts = self.engine.db.artifacts_for_job(task_run_id)
                # Check if step has a checkpoint
                nodes = snapshot.get("project_definition", {}).get("nodes", [])
                node_def = next((n for n in nodes if n["node_id"] == step["node_id"]), None)
                has_checkpoint = bool(node_def and node_def.get("checkpoint", {}).get("enabled"))

                if has_checkpoint:
                    from ..approvals.service import ApprovalService

                    approval_service = ApprovalService(self.db, self.engine, self.engine.config)
                    approval_service.create_pending_approval(project_run_id, step["node_id"])
                    self.db.update_project_step(
                        project_run_id,
                        step["node_id"],
                        active_task_run_id=task_run_id,
                        resolved_connections_json=json.dumps(
                            [
                                {
                                    "step_attempt": a.get("task_run_id"),
                                    "artifact_uid": a.get("artifact_uid"),
                                    "role": a.get("role"),
                                    "relative_path": a.get("relative_path"),
                                }
                                for a in artifacts
                            ]
                        ),
                    )
                else:
                    self.db.update_project_step(
                        project_run_id,
                        step["node_id"],
                        status="completed",
                        active_task_run_id=task_run_id,
                        completed_at=utc_now(),
                        resolved_connections_json=json.dumps(
                            [
                                {
                                    "step_attempt": a.get("task_run_id"),
                                    "artifact_uid": a.get("artifact_uid"),
                                    "role": a.get("role"),
                                    "relative_path": a.get("relative_path"),
                                }
                                for a in artifacts
                            ]
                        ),
                    )
            elif job_status in {"FAILED", "CANCELLED"}:
                self.db.update_project_step(
                    project_run_id,
                    step["node_id"],
                    status="failed",
                    active_task_run_id=task_run_id,
                    error_code=job.get("error_code"),
                    error_message=job.get("error_message"),
                    completed_at=utc_now(),
                )
                # Mark descendants as blocked so the run can finalize.
                self._block_descendants(project_run_id, spec, step["node_id"])

        # 2. Try to resolve inputs for pending/ready steps and mark ready when applicable.
        steps = self.db.list_project_steps(project_run_id)
        for step in steps:
            if step["status"] != "pending":
                continue
            deps_ok = self._dependencies_completed(spec, step["node_id"], step_by_id)
            if deps_ok:
                self.db.update_project_step(project_run_id, step["node_id"], status="ready")

        # 3. Atomically claim every ready step and dispatch each in turn.
        ready_step_ids = [s["node_id"] for s in self.db.list_project_steps(project_run_id) if s["status"] == "ready"]
        if ready_step_ids:
            claimed = self.db.claim_ready_steps(project_run_id, "ready", "queued")
            for _prid, node_id in claimed:
                if node_id in ready_step_ids:
                    self._dispatch_step(project_run_id, node_id, snapshot)

        # 4. After dispatch, any descendants whose deps are satisfied become ready.
        steps = self.db.list_project_steps(project_run_id)
        step_by_id = {s["node_id"]: s for s in steps}
        for step in steps:
            if step["status"] != "pending":
                continue
            if self._dependencies_completed(spec, step["node_id"], step_by_id):
                self.db.update_project_step(project_run_id, step["node_id"], status="ready")

        # 5. Finalize Project Run when appropriate.
        self._maybe_finalize(project_run_id, steps, spec)

    def _dependencies_completed(self, spec: ProjectSpec, node_id: str, step_by_id: dict[str, dict[str, Any]]) -> bool:
        for upstream_id in spec.predecessor_map()[node_id]:
            upstream = step_by_id.get(upstream_id)
            if not upstream or upstream["status"] != "completed":
                return False
        return True

    def _dispatch_step(self, project_run_id: str, node_id: str, project_snapshot: dict[str, Any]) -> None:
        step = self.db.get_project_step(project_run_id, node_id)
        if not step:
            return
        task_id = step["task_id"]
        task_snapshot = project_snapshot.get("task_snapshots", {}).get(task_id)
        if not task_snapshot:
            self.db.update_project_step(
                project_run_id,
                node_id,
                status="failed",
                error_code="PROJECT_TASK_MISSING",
                error_message=f"Task snapshot missing for {task_id}",
            )
            return

        # Resolve artifact inputs (connection-based and external).
        try:
            resolved_inputs = self.service.resolve_step_inputs(project_run_id, node_id)
        except RelayError as exc:
            self.db.update_project_step(
                project_run_id,
                node_id,
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
            )
            return

        try:
            worker_override = None
            try:
                prior_resolution = json.loads(step.get("resolved_connections_json") or "{}")
                if isinstance(prior_resolution, dict):
                    worker_override = prior_resolution.get("worker_override")
            except (TypeError, json.JSONDecodeError):
                pass
            request = JobRequest(
                task=task_snapshot.get("instructions") or "",
                caller="service",
                worker=worker_override or task_snapshot.get("default_worker") or "auto",
                artifact_inputs=[
                    {"artifact_uid": item["artifact_uid"], "alias": item["to_alias"]} for item in resolved_inputs
                ],
            )
            job, _reused = self.engine.run_task_from_snapshot(
                task_snapshot,
                request=request,
                queued=True,
                submitted_via="project",
                caller="service",
            )
        except RelayError as exc:
            self.db.update_project_step(
                project_run_id,
                node_id,
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
            )
            return

        self.db.append_project_step_run(project_run_id, node_id, job["job_id"], worker_override=None)
        self.db.update_project_step(
            project_run_id,
            node_id,
            status="running",
            active_task_run_id=job["job_id"],
            started_at=utc_now(),
            resolved_connections_json=json.dumps(resolved_inputs),
        )
        self.wake()

    def _block_descendants(self, project_run_id: str, spec: ProjectSpec, node_id: str) -> None:
        """Transition every transitive descendant of node_id to 'blocked'.

        Only descendants that are not already terminal are transitioned. Re-running
        a retry (which resets descendants to 'pending') will rescue them.
        """
        adjacency: dict[str, list[str]] = {n.node_id: [] for n in spec.nodes}
        for conn in spec.connections:
            adjacency.setdefault(conn.from_node, []).append(conn.to_node)
        stack = [node_id]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            for nxt in adjacency.get(current, []):
                if nxt in seen:
                    continue
                seen.add(nxt)
                step = self.db.get_project_step(project_run_id, nxt)
                if step and step["status"] not in {"completed", "failed", "cancelled", "blocked"}:
                    self.db.update_project_step(project_run_id, nxt, status="blocked", completed_at=utc_now())
                stack.append(nxt)

    def _maybe_finalize(self, project_run_id: str, steps: list[dict[str, Any]], spec: ProjectSpec) -> None:
        if not steps:
            return
        non_terminal = [s for s in steps if s["status"] not in _STEP_TERMINAL]
        if non_terminal:
            return
        if any(s["status"] == "failed" for s in steps):
            self._finalize_failed(project_run_id, steps)
            return
        if any(s["status"] != "completed" for s in steps):
            return  # cancelled/other transient
        self._finalize_completed(project_run_id, steps, spec)

    def _finalize_completed(self, project_run_id: str, steps: list[dict[str, Any]], spec: ProjectSpec) -> None:
        snapshot = json.loads(self.db.get_project_run(project_run_id)["project_snapshot_json"])
        selection = snapshot.get("output_selection", []) or []
        if not selection:
            self._mark_run_completed(project_run_id, steps, [], [])
            return
        final_ids: list[dict[str, Any]] = []
        warnings: list[str] = []
        for entry in selection:
            node_id = entry["node_id"]
            role = entry["role"]
            step = next((s for s in steps if s["node_id"] == node_id), None)
            if not step or not step.get("active_task_run_id"):
                self._mark_run_completed(
                    project_run_id,
                    steps,
                    final_ids,
                    [{"node_id": node_id, "role": role, "error": "PROJECT_ARTIFACT_MISSING"}],
                )
                return
            artifacts = self.engine.db.artifacts_for_job(step["active_task_run_id"])
            matches = [a for a in artifacts if a.get("role") == role]
            if len(matches) != 1:
                self._mark_run_completed(
                    project_run_id,
                    steps,
                    final_ids,
                    [
                        {
                            "node_id": node_id,
                            "role": role,
                            "matches": len(matches),
                            "error": "PROJECT_ARTIFACT_MISSING" if not matches else "PROJECT_ARTIFACT_AMBIGUOUS",
                        }
                    ],
                )
                return
            uid = matches[0].get("artifact_uid") or matches[0].get("relative_path")
            final_ids.append({"node_id": node_id, "role": role, "artifact_uid": uid})
        self._mark_run_completed(project_run_id, steps, final_ids, warnings)

    def _mark_run_completed(
        self,
        project_run_id: str,
        steps: list[dict[str, Any]],
        final_ids: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> None:
        failed = next((s for s in steps if s["status"] == "failed"), None)
        status = "failed" if failed or warnings else "completed"
        self.db.update_project_run(
            project_run_id,
            status=status,
            final_artifact_ids_json=json.dumps(final_ids),
            warnings_json=json.dumps(warnings),
            completed_at=utc_now(),
        )

    def _finalize_failed(self, project_run_id: str, steps: list[dict[str, Any]]) -> None:
        failed = next((s for s in steps if s["status"] == "failed"), None)
        warnings = []
        if failed:
            warnings.append(
                {
                    "node_id": failed["node_id"],
                    "error_code": failed.get("error_code"),
                    "error_message": failed.get("error_message"),
                }
            )
        self.db.update_project_run(
            project_run_id,
            status="failed",
            warnings_json=json.dumps(warnings),
            completed_at=utc_now(),
        )
        try:
            from ..notifications.service import NotificationService

            run = self.db.get_project_run(project_run_id)
            snapshot = json.loads(run["project_snapshot_json"]) if run else {}
            definition = snapshot.get("project_definition", {})
            NotificationService(self.db, self.engine.config).notify(
                project_run_id=project_run_id,
                trigger="on_failure",
                payload={
                    "project_run_id": project_run_id,
                    "status": "failed",
                    "warnings": warnings,
                },
                policy=definition.get("notification_policy") or {},
            )
        except Exception:  # notification delivery is best-effort
            logger.exception("project failure notification failed for %s", project_run_id)

    # --- daemon helpers ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {"running": bool(self._thread and self._thread.is_alive())}
