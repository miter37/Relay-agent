from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .adapters.base import AdapterContext
from .agent_registry import AgentRegistry
from .config import Config
from .db import Database
from .delivery import atomic_deliver_pair
from .errors import RelayError
from .models import JobRequest
from .process_supervisor import run_supervised
from .request_builder import build_request_markdown, copy_attachments, write_schema
from .security import validate_attachment_paths, validate_requested_paths
from .util import (
    ensure_dir,
    is_within,
    json_dump,
    local_date,
    new_job_id,
    safe_resolve,
    sha256_file,
    task_hash,
    utc_now,
)
from .validation import (
    materialize_artifact_payloads,
    reconcile_json_artifacts,
    scan_artifacts,
    validate_json_result,
    validate_text_result,
)

TECHNICAL_FALLBACK_CODES = {
    "WORKER_NOT_INSTALLED",
    "WORKER_DISABLED",
    "WORKER_UNVERIFIED",
    "WORKER_UNHEALTHY",
    "AUTH_REQUIRED",
    "RATE_LIMITED",
    "QUOTA_EXCEEDED",
    "TIMEOUT",
    "STALL_TIMEOUT",
    "INTERACTIVE_PROMPT_DETECTED",
    "PROCESS_CRASHED",
    "EMPTY_OUTPUT",
    "OUTPUT_NOT_CREATED",
    "INVALID_JSON",
    "SCHEMA_MISMATCH",
    "ARTIFACT_PATH_VIOLATION",
    "CAPABILITY_AUDIT_FAILED",
}

VALID_CALLERS = {"human", "hermes", "service", "schedule"}
VALID_SUBMITTED_VIA = {"cli", "gui", "hermes", "schedule", "legacy"}


class RelayEngine:
    def __init__(self, config: Config | None = None, db: Database | None = None):
        self.config = config or Config()
        self.config.init()
        self.db = db or Database(self.config.path_value("database_path"))
        self.spec_root = self.config.path_value("adapter_spec_root")
        self.agent_registry = AgentRegistry(self.config, self.spec_root)
        self._running_processes: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        per_worker = int(self.config.get("max_concurrent_per_worker", 1))
        self._per_worker_limit = per_worker
        self._worker_slots = {name: threading.Semaphore(per_worker) for name in ("claude", "codex", "antigravity")}
        self._worker_slots_lock = threading.Lock()

    def _worker_slot(self, worker: str) -> threading.Semaphore:
        with self._worker_slots_lock:
            return self._worker_slots.setdefault(worker, threading.Semaphore(self._per_worker_limit))

    def _resolve_request_task(self, request: JobRequest) -> None:
        request.caller = request.caller.strip().lower()
        if request.caller == "daemon":
            request.caller = "service"
        if request.caller not in VALID_CALLERS:
            raise RelayError("INVALID_REQUEST", f"Unsupported caller: {request.caller}")
        if request.task_file:
            path = safe_resolve(Path(request.task_file))
            if not path.is_file():
                raise RelayError("INVALID_REQUEST", f"Task file not found: {path}")
            request.task = path.read_text(encoding="utf-8")
        if not request.task or not request.task.strip():
            raise RelayError("TASK_REQUIRED", "A task string or --task-file is required")
        request.result_format = request.result_format.lower()
        if request.result_format not in {"json", "txt"}:
            raise RelayError("INVALID_REQUEST", "Result format must be json or txt")
        if request.worker != "auto":
            try:
                self.agent_registry.get_definition(request.worker)
            except KeyError:
                raise RelayError("INVALID_REQUEST", f"Unsupported worker: {request.worker}") from None

    def _history_display_mode(self) -> str:
        mode = str(self.config.get("history_display_mode") or self.config.get("history_mode", "metadata"))
        if mode not in {"full", "metadata"}:
            return "metadata"
        return mode

    @staticmethod
    def _short_text(value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 1)].rstrip() + "…"

    def _job_title_and_preview(self, request: JobRequest, job_id: str) -> tuple[str, str | None]:
        explicit = (request.title or "").strip()
        first_line = next((line.strip() for line in request.task.splitlines() if line.strip()), "")
        title = self._short_text(explicit or first_line or f"Job {job_id[:8]}", 60)
        preview = self._short_text(request.task, 240) if self._history_display_mode() == "full" else None
        return title, preview

    @staticmethod
    def _submitted_via(request: JobRequest, submitted_via: str | None) -> str:
        if submitted_via is None:
            return "hermes" if request.caller == "hermes" else "legacy"
        value = submitted_via.strip().lower()
        if value not in VALID_SUBMITTED_VIA:
            raise RelayError("INVALID_REQUEST", f"Unsupported submitted_via: {submitted_via}")
        return value

    def _default_paths(self, job_id: str, request: JobRequest) -> tuple[Path, Path]:
        ext = ".json" if request.result_format == "json" else ".txt"
        output = (
            safe_resolve(Path(request.output_path))
            if request.output_path
            else self.config.path_value("result_root") / local_date() / job_id / f"result{ext}"
        )
        artifacts = (
            safe_resolve(Path(request.artifact_path))
            if request.artifact_path
            else self.config.path_value("artifact_root") / job_id
        )
        return output, artifacts

    def create_job(
        self,
        request: JobRequest,
        queued: bool = False,
        submitted_via: str | None = None,
        *,
        schedule_id: str | None = None,
        scheduled_for: str | None = None,
        schedule_output_root: Path | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._resolve_request_task(request)
        self.config.reload()
        if schedule_id and request.caller.lower() != "schedule":
            raise RelayError("INVALID_REQUEST", "Only Schedule requests can link a Schedule ID.")
        if request.caller.lower() in {"hermes", "service", "daemon", "schedule"} and not self.config.get(
            "service_isolation_acknowledged", False
        ):
            raise RelayError(
                "PERMISSION_BLOCKED",
                "Hermes/service execution requires a dedicated low-privilege OS account. "
                "After configuring ACL isolation, run: relay config set service_isolation_acknowledged true",
            )
        validate_attachment_paths(self.config, request.caller, request.attachments)
        if request.workspace and request.caller.lower() in {"hermes", "service", "daemon", "schedule"}:
            workspace_root = safe_resolve(Path(request.workspace))
            if not is_within(workspace_root, self.config.path_value("workspace_root")):
                raise RelayError(
                    "WORKSPACE_PATH_NOT_ALLOWED",
                    f"Service workspace is outside the configured workspace root: {workspace_root}",
                )
        computed_hash = task_hash(
            request.task, request.attachments, request.profile, request.worker, request.result_format
        )
        if request.request_id:
            existing = self.db.get_by_request_id(request.request_id)
            if existing:
                if existing["task_hash"] != computed_hash:
                    raise RelayError(
                        "REQUEST_ID_CONFLICT",
                        f"request_id is already associated with a different task: {request.request_id}",
                    )
                return existing, True
        if not request.force_new:
            minutes = int(self.config.get("soft_dedup_window_minutes", 30))
            since = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
            existing = self.db.find_recent_task(computed_hash, since)
            action = self.config.get(
                "soft_dedup_hermes_action" if request.caller.lower() == "hermes" else "soft_dedup_human_action",
                "reuse" if request.caller.lower() == "hermes" else "warn",
            )
            if existing and action == "reuse":
                return existing, True
        job_id = new_job_id()
        output, artifacts = self._default_paths(job_id, request)
        validate_requested_paths(
            self.config,
            request.caller,
            output,
            artifacts,
            extra_output_roots=[str(schedule_output_root)] if schedule_output_root else (),
        )
        fallback = self.config.get("fallback_enabled", True) if request.fallback is None else request.fallback
        title, task_preview = self._job_title_and_preview(request, job_id)
        replayable = bool(self.config.get("store_replayable_requests", True))
        task_text = request.task if self._history_display_mode() == "full" else None
        row = {
            "job_id": job_id,
            "request_id": request.request_id,
            "caller": request.caller,
            "submitted_via": self._submitted_via(request, submitted_via),
            "task_hash": computed_hash,
            "task_text": task_text,
            "task_preview": task_preview,
            "title": title,
            "requested_worker": request.worker,
            "format": request.result_format,
            "profile": request.profile,
            "output_path": str(output),
            "artifact_path": str(artifacts),
            "status": "QUEUED" if queued else "CREATED",
            "fallback_enabled": 1 if fallback else 0,
            "request_json": json.dumps(request.to_dict(), ensure_ascii=False),
            "replayable": 1 if replayable else 0,
        }
        if schedule_id:
            row["schedule_id"] = schedule_id
            row["scheduled_for"] = scheduled_for
        try:
            self.db.create_job(row)
        except sqlite3.IntegrityError as exc:
            if not request.request_id:
                raise
            existing = self.db.get_by_request_id(request.request_id)
            if not existing:
                raise
            if existing["task_hash"] != computed_hash:
                raise RelayError(
                    "REQUEST_ID_CONFLICT",
                    f"request_id is already associated with a different task: {request.request_id}",
                ) from exc
            return existing, True
        self.db.add_event(job_id, "JOB_CREATED", {"queued": queued, "request_id": request.request_id})
        return self.db.get_job(job_id) or row, False

    def _worker_chain(self, job: dict[str, Any], request: JobRequest) -> list[str]:
        requested = request.worker
        fallback_order = request.fallback_agents
        if fallback_order is None:
            fallback_order = [str(x) for x in self.config.get("fallback_order", [])]
        if requested == "auto":
            chain = [str(self.config.get("default_worker", "claude"))]
            if job["fallback_enabled"]:
                chain.extend(fallback_order)
        else:
            chain = [requested]
            if job["fallback_enabled"]:
                chain.extend(x for x in fallback_order if x != requested)
        seen: set[str] = set()
        available = set(self.agent_registry.list_agent_ids())
        return [x for x in chain if x in available and not (x in seen or seen.add(x))]

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
        if job["status"] in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
            raise RelayError("JOB_NOT_CANCELLABLE", f"Job is already finished: {job_id}")
        if not self.db.request_cancel(job_id):
            if job["status"] == "CANCEL_REQUESTED":
                return {"ok": True, "job_id": job_id, "status": "CANCEL_REQUESTED", "changed": False}
            raise RelayError("JOB_NOT_CANCELLABLE", f"Job cannot be cancelled in state {job['status']}")
        updated = self.db.get_job(job_id) or job
        event = "JOB_CANCELLED" if updated["status"] == "CANCELLED" else "JOB_CANCEL_REQUESTED"
        self.db.add_event(job_id, event)
        return {"ok": True, "job_id": job_id, "status": updated["status"], "changed": True}

    def _prepare_workspace(self, job_id: str, worker: str, request: JobRequest) -> dict[str, Path]:
        workspace_root = (
            safe_resolve(Path(request.workspace)) if request.workspace else self.config.path_value("workspace_root")
        )
        workspace = workspace_root / worker / job_id
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        output_dir = ensure_dir(workspace / "output")
        artifact_dir = ensure_dir(workspace / "artifacts")
        runtime_dir = ensure_dir(workspace / "runtime")
        input_dir = ensure_dir(workspace / "input")
        result_file = output_dir / ("result.json.partial" if request.result_format == "json" else "result.txt.partial")
        schema_file = workspace / "schema.json"
        write_schema(schema_file)
        attachments = copy_attachments(request, input_dir)
        request_md = build_request_markdown(request, result_file, artifact_dir, attachments)
        request_file = workspace / "request.md"
        request_file.write_text(request_md, encoding="utf-8", newline="\n")
        json_dump(
            workspace / "relay-context.json",
            {
                "job_id": job_id,
                "result_format": request.result_format,
                "result_file": str(result_file),
                "artifact_dir": str(artifact_dir),
                "profile": request.profile,
                "attachments": attachments,
            },
        )
        return {
            "workspace": workspace,
            "runtime": runtime_dir,
            "result": result_file,
            "artifacts": artifact_dir,
            "request": request_file,
            "schema": schema_file,
        }

    def _cancel_requested(self, job_id: str) -> bool:
        row = self.db.get_job(job_id)
        return bool(row and row["status"] == "CANCEL_REQUESTED")

    def execute_job(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
        request = JobRequest.from_dict(json.loads(job["request_json"]))
        self._resolve_request_task(request)
        self.db.update_job(job_id, status="PREPARING", started_at=utc_now())
        self.db.add_event(job_id, "JOB_PREPARING")
        chain = self._worker_chain(job, request)
        errors: list[dict[str, Any]] = []
        for index, worker in enumerate(chain):
            worker_cfg = self.agent_registry.get_worker_config(worker)
            if not worker_cfg.get("enabled", False):
                err = RelayError("WORKER_DISABLED", f"Worker is disabled: {worker}")
                errors.append({"worker": worker, "code": err.code, "message": err.message})
                continue
            adapter = self.agent_registry.get_adapter(worker)
            try:
                spec = adapter.require_verified()
            except RelayError as err:
                errors.append({"worker": worker, "code": err.code, "message": err.message})
                if not job["fallback_enabled"]:
                    return self._fail_job(job_id, err.code, err.message, errors)
                continue
            try:
                paths = self._prepare_workspace(job_id, worker, request)
            except RelayError as err:
                errors.append({"worker": worker, "code": err.code, "message": err.message})
                return self._fail_job(job_id, err.code, err.message, errors)
            ctx = AdapterContext(
                job_id=job_id,
                workspace=paths["workspace"],
                request_file=paths["request"],
                result_file=paths["result"],
                artifact_dir=paths["artifacts"],
                schema_file=paths["schema"],
                result_format=request.result_format,
                profile=request.profile,
                model=request.model,
                config=worker_cfg,
            )
            try:
                command, stdin_bytes, env_extra = adapter.build_command(ctx)
            except RelayError as err:
                errors.append({"worker": worker, "code": err.code, "message": err.message})
                continue
            attempt_id = self.db.create_attempt(
                job_id,
                worker,
                worker_version=spec.version,
                adapter_spec_hash=adapter.spec_hash(spec),
                permission_mode=adapter.permission_mode(),
                sandbox_mode=adapter.sandbox_mode(),
                unattended_verified=1 if spec.unattended_ok else 0,
                stdout_path=str(paths["runtime"] / "stdout.log"),
                stderr_path=str(paths["runtime"] / "stderr.log"),
                command_json=json.dumps(command, ensure_ascii=False),
                fallback_reason=errors[-1]["code"] if errors else None,
            )
            self.db.update_job(job_id, status="RUNNING", actual_worker=worker)
            self.db.update_attempt(attempt_id, status="ACTIVE")
            self.db.add_event(job_id, "ATTEMPT_STARTED", {"worker": worker, "attempt": index + 1})
            timeout = request.timeout_seconds or int(self.config.get("timeout_seconds", 1200))
            slot = self._worker_slot(worker)
            slot.acquire()
            try:
                outcome = run_supervised(
                    command=command,
                    cwd=paths["workspace"],
                    stdin_bytes=stdin_bytes,
                    env_extra=env_extra,
                    stdout_path=paths["runtime"] / "stdout.log",
                    stderr_path=paths["runtime"] / "stderr.log",
                    timeout_seconds=timeout,
                    soft_stall_seconds=int(self.config.get("soft_stall_seconds", 120)),
                    hard_stall_seconds=int(self.config.get("hard_stall_seconds", 300)),
                    poll_seconds=float(self.config.get("poll_interval_seconds", 2)),
                    cancel_requested=lambda: self._cancel_requested(job_id),
                    event_callback=lambda event, payload: self.db.add_event(job_id, event, payload),
                )
            finally:
                slot.release()
            stderr_text = outcome.stderr_path.read_text(encoding="utf-8", errors="replace")
            if outcome.failure_code:
                code = outcome.failure_code
                message = f"{worker} ended with {code} after {outcome.duration_seconds:.1f}s"
                self.db.update_attempt(
                    attempt_id,
                    status="TERMINATED",
                    completed_at=utc_now(),
                    exit_code=outcome.exit_code,
                    failure_code=code,
                    failure_message=message,
                )
                if code == "CANCELLED":
                    self.db.update_job(
                        job_id, status="CANCELLED", error_code=code, error_message=message, completed_at=utc_now()
                    )
                    self.db.scrub_non_replayable(job_id)
                    return self.receipt(job_id)
                errors.append({"worker": worker, "code": code, "message": message})
                if job["fallback_enabled"] and code in TECHNICAL_FALLBACK_CODES:
                    continue
                return self._fail_job(job_id, code, message, errors)
            if outcome.exit_code not in (0, None):
                code, retryable = adapter.classify_failure(outcome.exit_code, stderr_text)
                message = f"{worker} exited with code {outcome.exit_code}"
                self.db.update_attempt(
                    attempt_id,
                    status="FAILED",
                    completed_at=utc_now(),
                    exit_code=outcome.exit_code,
                    failure_code=code,
                    failure_message=message,
                )
                errors.append({"worker": worker, "code": code, "message": message})
                if job["fallback_enabled"] and code in TECHNICAL_FALLBACK_CODES:
                    continue
                return self._fail_job(job_id, code, message, errors)
            try:
                adapter.normalize_output(ctx, outcome.stdout_path, outcome.stderr_path)
                self.db.update_job(job_id, status="VALIDATING")
                if request.result_format == "json":
                    value = validate_json_result(ctx.result_file, int(self.config.get("result_max_bytes")))
                else:
                    validate_text_result(ctx.result_file, int(self.config.get("result_max_bytes")))
                    value = None
                max_artifact_files = int(self.config.get("artifact_max_files", 200))
                max_artifact_bytes = int(self.config.get("artifact_max_total_bytes", 1024 * 1024 * 1024))
                materialized_artifacts: list[str] = []
                if value is not None:
                    materialized_artifacts = materialize_artifact_payloads(
                        value, ctx.artifact_dir, max_artifact_files, max_artifact_bytes
                    )
                artifact_records = scan_artifacts(ctx.artifact_dir, max_artifact_files, max_artifact_bytes)
                if value is not None:
                    value = reconcile_json_artifacts(value, artifact_records)
                    ctx.result_file.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                    result_status = value["status"]
                else:
                    result_status = "complete"
                if result_status == "failed":
                    raise RelayError("PROCESS_CRASHED", f"{worker} returned status=failed", False)
                self.db.update_job(job_id, status="DELIVERING")
                output_path = safe_resolve(Path(job["output_path"]))
                artifact_path = safe_resolve(Path(job["artifact_path"]))
                atomic_deliver_pair(
                    ctx.result_file,
                    output_path,
                    ctx.artifact_dir,
                    artifact_path,
                    overwrite=request.overwrite,
                )
                for item in artifact_records:
                    self.db.add_artifact(
                        job_id,
                        relative_path=item["relative_path"],
                        final_path=str(artifact_path / item["relative_path"]),
                        mime_type=item["mime_type"],
                        size=item["size"],
                        sha256=item["sha256"],
                    )
                receipt = {
                    "ok": True,
                    "status": "partial" if result_status == "partial" else "completed",
                    "job_id": job_id,
                    "worker": worker,
                    "result_path": str(output_path),
                    "artifact_path": str(artifact_path),
                    "result_status": result_status,
                    "uncertainties_count": len(value.get("uncertainties", [])) if value else None,
                    "missing_items_count": len(value.get("missing_items", [])) if value else None,
                    "result_sha256": sha256_file(output_path),
                    "artifacts_count": len(artifact_records),
                    "materialized_artifacts_count": len(materialized_artifacts),
                    "attempted_workers": [e["worker"] for e in errors] + [worker],
                    "content_verified": False,
                    "content_verification_note": "Relay verifies delivery and format, not factual accuracy.",
                }
                final_job_status = "PARTIAL" if result_status == "partial" else "COMPLETED"
                self.db.update_job(
                    job_id,
                    status=final_job_status,
                    result_status=result_status,
                    actual_worker=worker,
                    receipt_json=json.dumps(receipt, ensure_ascii=False),
                    completed_at=utc_now(),
                    error_code=None,
                    error_message=None,
                )
                self.db.update_attempt(
                    attempt_id,
                    status="SUCCEEDED",
                    completed_at=utc_now(),
                    exit_code=outcome.exit_code,
                )
                self.db.add_event(job_id, "JOB_COMPLETED", receipt)
                json_dump(output_path.parent / "relay-receipt.json", receipt)
                json_dump(artifact_path / "manifest.json", {"job_id": job_id, "artifacts": artifact_records})
                self.db.scrub_non_replayable(job_id)
                return receipt
            except RelayError as err:
                self.db.update_attempt(
                    attempt_id,
                    status="OUTPUT_INVALID"
                    if err.code in {"INVALID_JSON", "SCHEMA_MISMATCH", "EMPTY_OUTPUT", "OUTPUT_NOT_CREATED"}
                    else "FAILED",
                    completed_at=utc_now(),
                    exit_code=outcome.exit_code,
                    failure_code=err.code,
                    failure_message=err.message,
                )
                errors.append({"worker": worker, "code": err.code, "message": err.message})
                if job["fallback_enabled"] and err.code in TECHNICAL_FALLBACK_CODES:
                    continue
                return self._fail_job(job_id, err.code, err.message, errors)
        return self._fail_job(job_id, "ALL_WORKERS_FAILED", "All eligible workers failed", errors)

    def _fail_job(self, job_id: str, code: str, message: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
        attempt_rows = self.db.attempts_for_job(job_id)
        log_paths = [
            {"worker": row.get("worker"), "stdout": row.get("stdout_path"), "stderr": row.get("stderr_path")}
            for row in attempt_rows
        ]
        receipt = {
            "ok": False,
            "status": "failed",
            "job_id": job_id,
            "error_code": code,
            "error_message": message,
            "attempts": errors,
            "logs": log_paths,
            "content_verified": False,
        }
        self.db.update_job(
            job_id,
            status="FAILED",
            error_code=code,
            error_message=message,
            receipt_json=json.dumps(receipt, ensure_ascii=False),
            completed_at=utc_now(),
        )
        self.db.add_event(job_id, "JOB_FAILED", receipt)
        self.db.scrub_non_replayable(job_id)
        return receipt

    def run(self, request: JobRequest, submitted_via: str | None = None) -> dict[str, Any]:
        job, reused = self.create_job(request, queued=False, submitted_via=submitted_via)
        if reused:
            receipt = self.receipt(job["job_id"])
            receipt["deduplicated"] = True
            return receipt
        return self.execute_job(job["job_id"])

    def queue(self, request: JobRequest, submitted_via: str | None = None) -> dict[str, Any]:
        job, reused = self.create_job(request, queued=True, submitted_via=submitted_via)
        return {
            "ok": True,
            "status": "reused" if reused else "queued",
            "job_id": job["job_id"],
            "deduplicated": reused,
        }

    def queue_scheduled(
        self,
        request: JobRequest,
        *,
        schedule_id: str,
        scheduled_for: str,
        output_path: Path,
        artifact_path: Path,
        schedule_output_root: Path | None = None,
    ) -> dict[str, Any]:
        request.caller = "schedule"
        request.force_new = True
        request.output_path = str(output_path)
        request.artifact_path = str(artifact_path)
        job, reused = self.create_job(
            request,
            queued=True,
            submitted_via="schedule",
            schedule_id=schedule_id,
            scheduled_for=scheduled_for,
            schedule_output_root=schedule_output_root,
        )
        return {
            "ok": True,
            "status": "reused" if reused else "queued",
            "job_id": job["job_id"],
            "deduplicated": reused,
        }

    def receipt(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
        if job.get("receipt_json"):
            try:
                return json.loads(job["receipt_json"])
            except json.JSONDecodeError:
                pass
        return {
            "ok": job["status"] not in {"FAILED", "CANCELLED"},
            "status": job["status"].lower(),
            "job_id": job_id,
            "worker": job.get("actual_worker"),
            "result_path": job.get("output_path"),
            "artifact_path": job.get("artifact_path"),
            "error_code": job.get("error_code"),
            "error_message": job.get("error_message"),
        }

    def show(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
        job["attempts"] = self.db.attempts_for_job(job_id)
        job["events"] = self.db.events_for_job(job_id)
        job["artifacts"] = self.db.artifacts_for_job(job_id)
        job.pop("request_json", None)
        if self._history_display_mode() != "full":
            job.pop("task_text", None)
            job.pop("task_preview", None)
        return job

    def rerun(self, job_id: str, force_new: bool = True) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
        if not bool(job.get("replayable", 1)) or job.get("request_json") in (None, "", "{}"):
            raise RelayError("JOB_NOT_REPLAYABLE", "This job did not save a replayable request.")
        request = JobRequest.from_dict(json.loads(job["request_json"]))
        request.request_id = None
        request.force_new = force_new
        request.output_path = None
        request.artifact_path = None
        return self.run(request)

    def queue_rerun(self, job_id: str, submitted_via: str = "gui") -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
        if not bool(job.get("replayable", 1)) or job.get("request_json") in (None, "", "{}"):
            raise RelayError("JOB_NOT_REPLAYABLE", "This job did not save a replayable request.")
        request = JobRequest.from_dict(json.loads(job["request_json"]))
        request.request_id = None
        request.force_new = True
        request.output_path = None
        request.artifact_path = None
        request.caller = "human"
        result = self.queue(request, submitted_via=submitted_via)
        result["source_job_id"] = job_id
        return result
