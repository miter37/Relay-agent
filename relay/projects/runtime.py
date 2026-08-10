from __future__ import annotations

import json
import logging
import threading
from typing import Any

from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..models import JobRequest
from ..orchestrator.narration import (
    narrate_run_completed,
    narrate_run_started,
    narrate_step_completed,
    narrate_step_dispatched,
)
from ..orchestrator.overrides import apply_instruction_addendum, effective_output_role, parse_step_overrides
from ..orchestrator.supervisor import Supervisor
from ..util import utc_now
from .models import ProjectSpec
from .service import ProjectService

logger = logging.getLogger(__name__)


_STEP_TERMINAL = {"completed", "failed", "cancelled", "blocked"}
_TASK_SUCCESS_STATUSES = {"COMPLETED", "PARTIAL"}


class ProjectRuntime:
    def __init__(
        self,
        db: Database,
        engine: RelayEngine,
        service: ProjectService,
        *,
        tick_seconds: float = 0.5,
        supervisor: Supervisor | None = None,
    ):
        self.db = db
        self.engine = engine
        self.service = service
        self.tick_seconds = tick_seconds
        self.supervisor = supervisor or Supervisor(db, engine)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def _safe_hook(self, description: str, fn, *args, **kwargs) -> Any:
        """Run a narration/Orchestrator hook without ever letting it abort reconciliation."""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive, hooks are best-effort
            logger.exception("project runtime hook failed (%s): %s", description, exc)
            return None

    def _note(self, project_run_id: str, node_id: str | None, summary: str) -> None:
        self.db.append_project_run_event(project_run_id, node_id=node_id, kind="note", actor="runtime", summary=summary)

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
        orchestrator_config = Supervisor.config_from_snapshot(snapshot)
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
            step_run_status = {
                "QUEUED": "queued",
                "RUNNING": "running",
                "VALIDATING": "running",
                "DELIVERING": "running",
                "COMPLETED": "completed",
                "PARTIAL": "completed",
                "FAILED": "failed",
                "CANCELLED": "cancelled",
            }.get(job_status)
            if step_run_status:
                self.db.update_project_step_run(
                    task_run_id,
                    status=step_run_status,
                    completed_at=utc_now() if step_run_status in _STEP_TERMINAL else None,
                )
            if job_status in _TASK_SUCCESS_STATUSES:
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
                    if orchestrator_config:
                        completed_step = self.db.get_project_step(project_run_id, step["node_id"])
                        self._safe_hook(
                            "narrate_step_completed", self._note, project_run_id, step["node_id"],
                            narrate_step_completed(completed_step or step),
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
                if orchestrator_config:
                    self._safe_hook(
                        "supervisor.on_step_failed", self.supervisor.on_step_failed, project_run_id, step["node_id"]
                    )

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
        fresh_steps = self.db.list_project_steps(project_run_id)
        self._maybe_finalize(project_run_id, fresh_steps, spec)

    def _dependencies_completed(self, spec: ProjectSpec, node_id: str, step_by_id: dict[str, dict[str, Any]]) -> bool:
        for upstream_id in spec.predecessor_map()[node_id]:
            upstream = step_by_id.get(upstream_id)
            if not upstream or upstream["status"] != "completed":
                return False
        return True

    def _fail_step(
        self,
        project_run_id: str,
        node_id: str,
        spec: ProjectSpec,
        code: str,
        message: str | None,
        *,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> None:
        """Mark a step failed and block its descendants.

        Without blocking, a step that fails before it ever produced a Task Run
        leaves its descendants 'pending' forever, so the Project Run never reaches
        a terminal state and cannot even be retried.
        """
        self.db.update_project_step(
            project_run_id,
            node_id,
            status="failed",
            error_code=code,
            error_message=message,
        )
        self._block_descendants(project_run_id, spec, node_id)
        if orchestrator_config:
            self._safe_hook("supervisor.on_step_failed", self.supervisor.on_step_failed, project_run_id, node_id)

    def _dispatch_step(self, project_run_id: str, node_id: str, project_snapshot: dict[str, Any]) -> None:
        step = self.db.get_project_step(project_run_id, node_id)
        if not step:
            return
        spec = ProjectSpec.from_dict(project_snapshot["project_definition"])
        orchestrator_config = Supervisor.config_from_snapshot(project_snapshot)
        task_id = step["task_id"]
        task_snapshot = project_snapshot.get("task_snapshots", {}).get(task_id)
        if not task_snapshot:
            self._fail_step(
                project_run_id,
                node_id,
                spec,
                "PROJECT_TASK_MISSING",
                f"Task snapshot missing for {task_id}",
                orchestrator_config=orchestrator_config,
            )
            return

        # Resolve artifact inputs (connection-based and external).
        try:
            resolved_inputs = self.service.resolve_step_inputs(project_run_id, node_id)
        except RelayError as exc:
            self._fail_step(project_run_id, node_id, spec, exc.code, exc.message, orchestrator_config=orchestrator_config)
            return

        try:
            step_overrides = parse_step_overrides(step.get("step_overrides_json"))
            worker_override = step_overrides.get("worker_override")
            if worker_override is None:
                # Legacy rows written before schema v15 stashed the override directly in
                # resolved_connections_json; the migration backfill moves these on the next
                # Database() open, but this keeps an in-session row dispatchable too.
                try:
                    prior_resolution = json.loads(step.get("resolved_connections_json") or "{}")
                    if isinstance(prior_resolution, dict):
                        worker_override = prior_resolution.get("worker_override")
                except (TypeError, json.JSONDecodeError):
                    pass
            instructions = apply_instruction_addendum(
                task_snapshot.get("instructions") or "", step_overrides.get("instruction_addendum")
            )
            request = JobRequest(
                task=instructions,
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
            self._fail_step(project_run_id, node_id, spec, exc.code, exc.message, orchestrator_config=orchestrator_config)
            return

        self.db.append_project_step_run(project_run_id, node_id, job["job_id"], worker_override=None)
        now = utc_now()
        self.db.update_project_step(
            project_run_id,
            node_id,
            status="running",
            active_task_run_id=job["job_id"],
            started_at=now,
            resolved_connections_json=json.dumps(resolved_inputs),
            step_overrides_json=None,
        )
        run_started = self.db.ensure_project_run_started(project_run_id, now)
        if orchestrator_config:
            if run_started:
                self._safe_hook("narrate_run_started", self._note, project_run_id, None, narrate_run_started(spec))
            is_retry = bool(step_overrides.get("worker_override") or step_overrides.get("instruction_addendum"))
            self._safe_hook(
                "narrate_step_dispatched", self._note, project_run_id, node_id,
                narrate_step_dispatched(node_id, retry=is_retry),
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
        orchestrator_config = Supervisor.config_from_snapshot(snapshot)
        selection = snapshot.get("output_selection", []) or []
        final_ids: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for step in steps:
            task_run_id = step.get("active_task_run_id")
            task_run = self.engine.db.get_job(task_run_id) if task_run_id else None
            if task_run and task_run.get("status") == "PARTIAL":
                warnings.append(
                    {
                        "node_id": step["node_id"],
                        "task_run_id": task_run_id,
                        "warning": "TASK_RUN_PARTIAL",
                    }
                )
        if not selection:
            self._mark_run_completed(project_run_id, steps, [], warnings, orchestrator_config=orchestrator_config)
            return
        for entry in selection:
            node_id = entry["node_id"]
            role = entry["role"]
            step = next((s for s in steps if s["node_id"] == node_id), None)
            if step:
                step_overrides = parse_step_overrides(step.get("step_overrides_json"))
                role = effective_output_role(role, step_overrides.get("output_role_override"))
            if not step or not step.get("active_task_run_id"):
                self._mark_run_completed(
                    project_run_id,
                    steps,
                    final_ids,
                    [{"node_id": node_id, "role": role, "error": "PROJECT_ARTIFACT_MISSING"}],
                    failed=True,
                )
                return
            artifacts = self.engine.db.artifacts_for_job(step["active_task_run_id"])
            matches = [a for a in artifacts if a.get("role") == role]
            if len(matches) != 1:
                if orchestrator_config and not matches:
                    # Only a clean "nothing matched" case is repairable; an ambiguous
                    # multi-match needs judgment the deterministic tier won't guess at,
                    # and plan_repair already declines it (see PROJECT_ARTIFACT_AMBIGUOUS).
                    decision = self._safe_hook(
                        "supervisor.on_output_selection_failed",
                        self.supervisor.on_output_selection_failed,
                        project_run_id, node_id, role,
                    )
                    if decision:
                        return  # Step reset to pending; the run stays 'running' and re-finalizes next tick.
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
                    failed=True,
                )
                return
            uid = matches[0].get("artifact_uid") or matches[0].get("relative_path")
            final_ids.append({"node_id": node_id, "role": role, "artifact_uid": uid})
        self._mark_run_completed(project_run_id, steps, final_ids, warnings, orchestrator_config=orchestrator_config)

    def _mark_run_completed(
        self,
        project_run_id: str,
        steps: list[dict[str, Any]],
        final_ids: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
        *,
        failed: bool = False,
        orchestrator_config: dict[str, Any] | None = None,
    ) -> None:
        failed_step = next((s for s in steps if s["status"] == "failed"), None)
        status = "failed" if failed or failed_step else "completed"
        self.db.update_project_run(
            project_run_id,
            status=status,
            final_artifact_ids_json=json.dumps(final_ids),
            warnings_json=json.dumps(warnings),
            completed_at=utc_now(),
        )
        if status == "completed" and orchestrator_config:
            run = self.db.get_project_run(project_run_id)
            if run:
                self._safe_hook(
                    "narrate_run_completed", self._note, project_run_id, None,
                    narrate_run_completed(run, steps, final_ids),
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
        self._safe_hook("orchestrator_closing_report", self._maybe_write_closing_report, project_run_id, warnings)
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

    def _maybe_write_closing_report(self, project_run_id: str, warnings: list[dict[str, Any]]) -> None:
        """Ask the Orchestrator agent for a short closing explanation of a failed Run.

        Only called when the Orchestrator is attached and the Run actually failed - that
        failure is itself the incident being explained. A successful Run never reaches
        here; its story is already fully told by ``narrate_run_completed``.
        """
        run = self.db.get_project_run(project_run_id)
        if not run:
            return
        snapshot = json.loads(run["project_snapshot_json"])
        config = Supervisor.config_from_snapshot(snapshot)
        if not config:
            return
        agent = self.supervisor.build_agent(config)
        state_digest = self.supervisor.state_digest(project_run_id)
        run_summary = f"status=failed warnings={json.dumps(warnings)}"
        report = agent.final_report(state_digest, run_summary)
        self.db.append_project_run_event(
            project_run_id, node_id=None, kind="report", actor="orchestrator", summary=report
        )

    # --- daemon helpers ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {"running": bool(self._thread and self._thread.is_alive())}
