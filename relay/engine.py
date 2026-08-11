from __future__ import annotations

import json
import logging
import os
import re
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
from .models import JobRequest, TaskSpec
from .process_supervisor import run_supervised
from .profiles import ProfileStore
from .receipts import RECEIPT_SCHEMA_VERSION
from .request_builder import build_request_markdown, copy_attachments, write_schema
from .security import validate_attachment_paths, validate_requested_paths
from .target_workspace import (
    TargetWorkspace,
    apply_delta,
    calculate_delta,
    copy_delta_to_artifacts,
    infer_target_path,
    prepare_target_workspace,
    resolve_target_path,
    target_fingerprint,
    validate_target_path,
)
from .task_inputs import validate_inputs
from .util import (
    canonical_json,
    ensure_dir,
    is_within,
    json_dump,
    local_date,
    new_artifact_uid,
    new_job_id,
    safe_resolve,
    sha256_bytes,
    sha256_file,
    task_hash,
    utc_now,
)
from .validation import (
    materialize_artifact_payloads,
    normalize_declared_roles,
    normalize_summary,
    reconcile_json_artifacts,
    scan_artifacts,
    validate_json_result,
    validate_text_result,
)


def _decode_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


logger = logging.getLogger(__name__)

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
VALID_SUBMITTED_VIA = {"cli", "gui", "hermes", "schedule", "legacy", "project", "routine", "orchestrator"}
VALID_TRIGGER_TYPES = {"manual", "api", "schedule", "rerun", "project", "routine"}


class RelayEngine:
    def __init__(self, config: Config | None = None, db: Database | None = None):
        self.config = config or Config()
        self.config.init()
        self.db = db or Database(self.config.path_value("database_path"))
        self.spec_root = self.config.path_value("adapter_spec_root")
        self.agent_registry = AgentRegistry(self.config, self.spec_root)
        self.profiles = ProfileStore(self.config)
        self._running_processes: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._progress: dict[str, dict[str, Any]] = {}
        self._progress_lock = threading.Lock()
        per_worker = int(self.config.get("max_concurrent_per_worker", 1))
        self._per_worker_limit = per_worker
        self._worker_slots = {name: threading.Semaphore(per_worker) for name in ("claude", "codex", "antigravity")}
        self._worker_slots_lock = threading.Lock()
        from .projects.service import ProjectService

        self.project_service = ProjectService(self.db, self)
        self.routine_service = None  # wired by RelayDaemon to keep engine config-free

    def _set_progress(self, job_id: str, **changes: Any) -> None:
        with self._progress_lock:
            self._progress[job_id] = {**self._progress.get(job_id, {}), **changes}

    def progress_for_job(self, job_id: str) -> dict[str, Any] | None:
        with self._progress_lock:
            value = self._progress.get(job_id)
            return dict(value) if value else None

    def _clear_progress(self, job_id: str) -> None:
        with self._progress_lock:
            self._progress.pop(job_id, None)

    def _worker_slot(self, worker: str) -> threading.Semaphore:
        with self._worker_slots_lock:
            slot = self._worker_slots.get(worker)
            if slot is None:
                slot = threading.Semaphore(self._per_worker_limit)
                self._worker_slots[worker] = slot
            return slot

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
        if not isinstance(request.inputs, dict):
            raise RelayError("INVALID_REQUEST", "Task inputs must be a JSON object.")
        request.result_format = request.result_format.lower()
        if request.result_format not in {"json", "txt"}:
            raise RelayError("INVALID_REQUEST", "Result format must be json or txt")
        if request.worker != "auto":
            try:
                self.agent_registry.get_definition(request.worker)
            except KeyError:
                raise RelayError("INVALID_REQUEST", f"Unsupported worker: {request.worker}") from None
        profile = self.profiles.get(request.profile)
        request.profile = profile["profile_id"]
        request.profile_snapshot = {
            key: profile[key]
            for key in ("profile_id", "name", "description", "instructions", "builtin", "updated_at")
            if key in profile
        }

    @staticmethod
    def _validate_task_inputs(request: JobRequest, task_definition: dict[str, Any] | None) -> None:
        if not task_definition or not task_definition.get("input_schema"):
            return
        try:
            request.inputs = validate_inputs(request.inputs, task_definition["input_schema"])
        except ValueError as exc:
            raise RelayError("INPUT_SCHEMA_MISMATCH", str(exc)) from exc

    def _history_display_mode(self) -> str:
        mode = str(self.config.get("history_display_mode") or self.config.get("history_mode", "metadata"))
        if mode not in {"full", "metadata"}:
            return "metadata"
        return mode

    def _resolve_task_summary(self, request: JobRequest, task_definition: dict[str, Any] | None = None) -> str | None:
        if task_definition:
            candidate = (
                task_definition.get("task_summary")
                or task_definition.get("description")
                or task_definition.get("instructions")
            )
        elif self._history_display_mode() == "full":
            candidate = request.task
        else:
            candidate = None
        return normalize_summary(candidate, max_chars=500, field="task_summary", error_code="TASK_INVALID")

    @staticmethod
    def _resolve_result_summary(value: dict[str, Any] | None, text: str | None) -> str | None:
        candidate = (value or {}).get("summary") or (value or {}).get("answer") or text
        return normalize_summary(candidate, max_chars=1000, field="result_summary", error_code="SCHEMA_MISMATCH")

    @staticmethod
    def _resolve_failure_reason(job: dict[str, Any], fallback: str | None = None) -> str | None:
        candidate = job.get("error_message") or fallback
        if not candidate and job.get("status") == "CANCELLED":
            candidate = "Task Run was cancelled."
        return normalize_summary(candidate, max_chars=1000, field="failure_reason", error_code="INTERNAL_ERROR")

    @staticmethod
    def _receipt_summary(job: dict[str, Any], key: str) -> str | None:
        return job.get(key) if bool(job.get("replayable", 1)) else None

    @staticmethod
    def _short_text(value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 1)].rstrip() + "…"

    def _job_title_and_preview(self, request: JobRequest, job_id: str) -> tuple[str, str | None]:
        explicit = (request.title or "").strip()
        first_line = next((line.strip() for line in request.task.splitlines() if line.strip()), "")
        title = self._short_text(explicit or first_line or f"Task Run {job_id[:8]}", 60)
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

    @staticmethod
    def _trigger_type(request: JobRequest, submitted_via: str | None, *, schedule_id: str | None) -> str:
        if schedule_id or request.caller == "schedule" or submitted_via == "schedule":
            return "schedule"
        if submitted_via in {"cli", "gui", "hermes"}:
            return "api" if request.caller in {"hermes", "service"} else "manual"
        return "manual"

    @staticmethod
    def _task_snapshot(
        request: JobRequest,
        *,
        trigger_type: str,
        artifact_inputs: list[dict[str, Any]] | None = None,
        task_definition: dict[str, Any] | None = None,
    ) -> str:
        snapshot = {
            "task": request.task,
            "task_file": request.task_file,
            "attachments": list(request.attachments),
            "inputs": dict(request.inputs or {}),
            "artifact_inputs": artifact_inputs or [],
            "worker": request.worker,
            "fallback": request.fallback,
            "fallback_agents": list(request.fallback_agents) if request.fallback_agents else None,
            "result_format": request.result_format,
            "profile": request.profile,
            "profile_snapshot": request.profile_snapshot,
            "timeout_seconds": request.timeout_seconds,
            "model": request.model,
            "trigger_type": trigger_type,
            "review_policy": task_definition.get("review_policy") if task_definition else None,
        }
        if task_definition:
            snapshot["task_id"] = task_definition.get("task_id")
            snapshot["task_name"] = task_definition.get("name")
            snapshot["task_version"] = task_definition.get("version")
            snapshot["task_definition"] = task_definition
        return canonical_json(snapshot)

    def _resolve_artifact_inputs(self, request: JobRequest) -> list[dict[str, Any]]:
        if not request.artifact_inputs:
            return []
        resolved: list[dict[str, Any]] = []
        aliases: set[str] = set()
        for item in request.artifact_inputs:
            if not isinstance(item, dict):
                raise RelayError("INVALID_REQUEST", "Each artifact input must be an object.")
            uid = str(item.get("artifact_uid") or "").strip()
            alias = str(item.get("alias") or "").strip().upper()
            if not uid or not alias or not re.fullmatch(r"A[1-9][0-9]*", alias):
                raise RelayError(
                    "INVALID_REQUEST", "Artifact inputs require a valid artifact_uid and alias such as A1."
                )
            if alias in aliases:
                raise RelayError("INVALID_REQUEST", f"Artifact alias is duplicated: {alias}")
            aliases.add(alias)
            artifact = self.db.artifact_by_uid(uid)
            if not artifact:
                raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {uid}")
            source = safe_resolve(Path(str(artifact["final_path"])))
            if not source.is_file():
                raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact file is not available: {source}")
            # Both roots are Relay-managed storage. `result_root` holds the delivered
            # result file, which is registered as the `result`-role Artifact and is the
            # only role guaranteed to be unique per Run, so Project connections must be
            # able to bind it. The check still refuses arbitrary filesystem paths.
            managed_roots = (self.config.path_value("artifact_root"), self.config.path_value("result_root"))
            if not any(is_within(source, root) for root in managed_roots):
                raise RelayError("ARTIFACT_PATH_VIOLATION", f"Artifact is outside Relay artifact storage: {source}")
            size = source.stat().st_size
            digest = sha256_file(source)
            if size != int(artifact["size"]) or digest != artifact["sha256"]:
                raise RelayError("ARTIFACT_CHANGED", f"Artifact content changed: {uid}")
            resolved.append(
                {
                    "artifact_uid": uid,
                    "alias": alias,
                    "source_job_id": artifact["job_id"],
                    "source_relative_path": artifact["relative_path"],
                    "source_final_path": str(source),
                    "source_sha256": digest,
                    "source_size": size,
                    "role": artifact.get("role") or "output",
                    "mime_type": artifact.get("mime_type"),
                }
            )
        return resolved

    def _stage_artifact_inputs(self, job_id: str, resolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not resolved:
            return []
        snapshot_root = ensure_dir(self.config.path_value("input_snapshot_root") / job_id)
        manifest: list[dict[str, Any]] = []
        for item in resolved:
            source = Path(item["source_final_path"])
            name = Path(item["source_relative_path"]).name or f"artifact-{item['artifact_uid']}"
            destination = snapshot_root / f"{item['alias']}-{name}"
            shutil.copy2(source, destination)
            digest = sha256_file(destination)
            size = destination.stat().st_size
            if digest != item["source_sha256"] or size != item["source_size"]:
                raise RelayError("ARTIFACT_CHANGED", f"Artifact snapshot verification failed: {item['artifact_uid']}")
            item = {
                **item,
                "snapshot_relative_path": str(destination.relative_to(self.config.home)),
                "snapshot_path": str(destination),
                "snapshot_sha256": digest,
                "snapshot_size": size,
                "binding_mode": "snapshot",
            }
            manifest.append(item)
        return manifest

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
        trigger_type: str | None = None,
        task_id: str | None = None,
        task_definition: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        self._resolve_request_task(request)
        self._validate_task_inputs(request, task_definition)
        self.config.reload()
        resolved_inputs = self._resolve_artifact_inputs(request)
        # Service-type callers (Project/Routine/Orchestrator dispatch, Schedules) can
        # never use working-folder mode - see the TARGET_PATH_NOT_ALLOWED check below -
        # so skip inference for them entirely rather than raising a spurious
        # TARGET_PATH_AMBIGUOUS/NOT_ALLOWED from a filesystem path that happens to
        # appear in task text Relay generated itself (e.g. the Orchestrator's own
        # prompt, which embeds raw worker log/error text that can contain paths).
        is_service_caller = request.caller.lower() in {"hermes", "service", "daemon", "schedule"}
        requested_target = request.target_path or (None if is_service_caller else infer_target_path(request.task))
        target = resolve_target_path(requested_target) if requested_target else None
        request.target_path = str(target) if target else None
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
        if target and request.caller.lower() in {"hermes", "service", "daemon", "schedule"}:
            raise RelayError(
                "TARGET_PATH_NOT_ALLOWED",
                "Working-folder updates are available only for interactive CLI and GUI Task Runs.",
            )
        if request.workspace and request.caller.lower() in {"hermes", "service", "daemon", "schedule"}:
            workspace_root = safe_resolve(Path(request.workspace))
            if not is_within(workspace_root, self.config.path_value("workspace_root")):
                raise RelayError(
                    "WORKSPACE_PATH_NOT_ALLOWED",
                    f"Service workspace is outside the configured workspace root: {workspace_root}",
                )
        computed_hash = task_hash(
            request.task,
            request.attachments,
            request.profile,
            request.worker,
            request.result_format,
            request.inputs,
        )
        if target:
            validate_target_path(target, self.config.home, ())
            computed_hash = sha256_bytes(
                canonical_json(
                    {
                        "task_hash": computed_hash,
                        "target_path": os.path.normcase(str(target)),
                        "target_state": target_fingerprint(target),
                    }
                ).encode("utf-8")
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
        resolved_inputs = self._stage_artifact_inputs(job_id, resolved_inputs)
        output, artifacts = self._default_paths(job_id, request)
        validate_requested_paths(
            self.config,
            request.caller,
            output,
            artifacts,
            extra_output_roots=[str(schedule_output_root)] if schedule_output_root else (),
        )
        if target:
            validate_target_path(target, self.config.home, (output, artifacts))
        fallback = self.config.get("fallback_enabled", True) if request.fallback is None else request.fallback
        title, task_preview = self._job_title_and_preview(request, job_id)
        task_summary = self._resolve_task_summary(request, task_definition)
        replayable = bool(self.config.get("store_replayable_requests", True))
        task_text = request.task if self._history_display_mode() == "full" else None
        submitted_source = self._submitted_via(request, submitted_via)
        resolved_trigger = trigger_type or self._trigger_type(request, submitted_source, schedule_id=schedule_id)
        if resolved_trigger not in VALID_TRIGGER_TYPES:
            raise RelayError("INVALID_REQUEST", f"Unsupported trigger_type: {resolved_trigger}")
        row = {
            "job_id": job_id,
            "request_id": request.request_id,
            "caller": request.caller,
            "submitted_via": submitted_source,
            "trigger_type": resolved_trigger,
            "task_id": task_id,
            "task_summary": task_summary,
            "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
            "review_status": "not_started",
            "review_id": request.review_id,
            "review_policy_json": self._effective_review_policy(request, task_definition),
            "task_snapshot_json": self._task_snapshot(
                request, trigger_type=resolved_trigger, artifact_inputs=resolved_inputs, task_definition=task_definition
            ),
            "input_manifest_json": canonical_json(
                [
                    {
                        key: value
                        for key, value in item.items()
                        if key
                        in {
                            "alias",
                            "artifact_uid",
                            "source_job_id",
                            "source_relative_path",
                            "source_sha256",
                            "source_size",
                            "snapshot_relative_path",
                            "snapshot_sha256",
                            "snapshot_size",
                            "binding_mode",
                            "role",
                            "mime_type",
                        }
                    }
                    for item in resolved_inputs
                ]
            )
            if resolved_inputs
            else None,
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
        for item in resolved_inputs:
            self.db.add_lineage(
                {
                    "consumer_job_id": job_id,
                    "source_artifact_uid": item["artifact_uid"],
                    "source_job_id": item["source_job_id"],
                    "alias": item["alias"],
                    "binding_mode": item["binding_mode"],
                    "source_relative_path": item["source_relative_path"],
                    "source_sha256": item["source_sha256"],
                    "source_size": item["source_size"],
                    "snapshot_relative_path": item["snapshot_relative_path"],
                    "snapshot_sha256": item["snapshot_sha256"],
                    "snapshot_size": item["snapshot_size"],
                }
            )
        self.db.add_event(job_id, "JOB_CREATED", {"queued": queued, "request_id": request.request_id})
        return self.db.get_job(job_id) or row, False

    @staticmethod
    def _effective_review_policy(request: JobRequest, task_definition: dict[str, Any] | None) -> str | None:
        mode = str(request.review_mode or "inherit").casefold()
        if mode not in {"inherit", "human", "off"}:
            raise RelayError("INVALID_REQUEST", "review_mode must be inherit, human, or off.")
        if mode == "off" or request.caller.lower() in {"service", "schedule", "daemon"}:
            return None
        if mode == "human":
            return json.dumps({"enabled": True, "reviewer": "human"}, ensure_ascii=False)
        policy = (task_definition or {}).get("review_policy") if task_definition else None
        if not policy:
            return None
        return policy if isinstance(policy, str) else json.dumps(policy, ensure_ascii=False)

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
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
        if job["status"] in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
            raise RelayError("JOB_NOT_CANCELLABLE", f"Task Run is already finished: {job_id}")
        if not self.db.request_cancel(job_id):
            if job["status"] == "CANCEL_REQUESTED":
                return {
                    "ok": True,
                    "job_id": job_id,
                    "task_run_id": job_id,
                    "status": "CANCEL_REQUESTED",
                    "changed": False,
                }
            raise RelayError("JOB_NOT_CANCELLABLE", f"Task Run cannot be cancelled in state {job['status']}")
        updated = self.db.get_job(job_id) or job
        event = "JOB_CANCELLED" if updated["status"] == "CANCELLED" else "JOB_CANCEL_REQUESTED"
        self.db.add_event(job_id, event)
        return {
            "ok": True,
            "job_id": job_id,
            "task_run_id": job_id,
            "status": updated["status"],
            "changed": True,
        }

    def _prepare_workspace(
        self,
        job_id: str,
        worker: str,
        request: JobRequest,
        artifact_inputs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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
        target_workspace: TargetWorkspace | None = None
        if request.target_path:
            target_workspace = prepare_target_workspace(
                resolve_target_path(request.target_path),
                workspace / "target",
            )
        result_file = output_dir / ("result.json.partial" if request.result_format == "json" else "result.txt.partial")
        schema_file = workspace / "schema.json"
        write_schema(schema_file)
        attachments = copy_attachments(request, input_dir)
        artifact_input_files: list[dict[str, Any]] = []
        for item in artifact_inputs or []:
            source = Path(item["snapshot_path"])
            destination = input_dir / Path(item["snapshot_relative_path"]).name
            shutil.copy2(source, destination)
            artifact_input_files.append({**item, "workspace_path": str(destination)})
        request_md = build_request_markdown(
            request,
            result_file,
            artifact_dir,
            attachments,
            target_workspace.working_copy if target_workspace else None,
            artifact_input_files,
        )
        request.resolved_artifact_inputs = artifact_input_files

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
                "target_working_copy": str(target_workspace.working_copy) if target_workspace else None,
            },
        )
        return {
            "workspace": workspace,
            "runtime": runtime_dir,
            "result": result_file,
            "artifacts": artifact_dir,
            "request": request_file,
            "schema": schema_file,
            "target": target_workspace,
        }

    def _cancel_requested(self, job_id: str) -> bool:
        row = self.db.get_job(job_id)
        return bool(row and row["status"] == "CANCEL_REQUESTED")

    def execute_job(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
        request = JobRequest.from_dict(json.loads(job["request_json"]))
        self._resolve_request_task(request)
        review_policy = _decode_json_object(job.get("review_policy_json"))
        review_enabled = bool(review_policy and review_policy.get("enabled"))
        input_manifest = json.loads(job.get("input_manifest_json") or "[]")
        if not isinstance(input_manifest, list):
            input_manifest = []
        input_manifest = [
            {**item, "snapshot_path": str(self.config.home / item["snapshot_relative_path"])}
            for item in input_manifest
            if isinstance(item, dict) and item.get("snapshot_relative_path")
        ]
        self._set_progress(job_id, stage="preparing", process_alive=None)
        self.db.update_job(job_id, status="PREPARING", started_at=utc_now())
        self.db.add_event(job_id, "JOB_PREPARING")
        chain = self._worker_chain(job, request)
        errors: list[dict[str, Any]] = []
        for index, worker in enumerate(chain):
            self._set_progress(job_id, stage="preparing", worker=worker, process_alive=None)
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
                paths = self._prepare_workspace(job_id, worker, request, input_manifest)
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
                # A model ID is provider-specific: it was chosen for the first
                # (intended) worker in the chain, so passing it unchanged to a
                # fallback worker on a different provider fails outright (e.g. a
                # Gemini model ID rejected by Codex) instead of actually falling
                # back. Only the primary attempt gets the requested model; a
                # fallback worker uses its own default.
                model=request.model if index == 0 else None,
                config=worker_cfg,
            )
            try:
                command, stdin_bytes, env_extra = adapter.build_command(ctx)
                if paths.get("target"):
                    env_extra = {**env_extra, "RELAY_TARGET_DIR": str(paths["target"].working_copy)}
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
            self._set_progress(
                job_id,
                stage="running",
                worker=worker,
                attempt_id=attempt_id,
                process_alive=None,
                soft_stall_seconds=int(self.config.get("soft_stall_seconds", 120)),
                hard_stall_seconds=int(self.config.get("hard_stall_seconds", 300)),
            )
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
                    base_env=adapter.subprocess_environment(),
                    cancel_requested=lambda: self._cancel_requested(job_id),
                    event_callback=lambda event, payload: self.db.add_event(job_id, event, payload),
                    progress_callback=lambda snapshot, current_worker=worker, current_attempt_id=attempt_id: (
                        self._set_progress(
                            job_id,
                            stage="running",
                            worker=current_worker,
                            attempt_id=current_attempt_id,
                            **snapshot,
                        )
                    ),
                )
            finally:
                slot.release()
            stderr_text = outcome.stderr_path.read_text(encoding="utf-8", errors="replace")
            permission_error = adapter.has_permission_error(stderr_text)
            if outcome.failure_code:
                code = "PERMISSION_BLOCKED" if permission_error else outcome.failure_code
                message = (
                    adapter.permission_failure_message(f"{worker} reported an access or sandbox permission error")
                    if permission_error
                    else f"{worker} ended with {code} after {outcome.duration_seconds:.1f}s"
                )
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
                        job_id,
                        status="CANCELLED",
                        error_code=code,
                        error_message=message,
                        result_summary=None,
                        completed_at=utc_now(),
                    )
                    self.db.scrub_non_replayable(job_id)
                    self._refresh_search_index(job_id)
                    self._clear_progress(job_id)
                    return self.receipt(job_id)
                errors.append({"worker": worker, "code": code, "message": message})
                if job["fallback_enabled"] and code in TECHNICAL_FALLBACK_CODES:
                    continue
                return self._fail_job(job_id, code, message, errors)
            if outcome.exit_code not in (0, None):
                code, retryable = adapter.classify_failure(outcome.exit_code, stderr_text)
                message = (
                    adapter.permission_failure_message(f"{worker} reported an access or sandbox permission error")
                    if code == "PERMISSION_BLOCKED"
                    else f"{worker} exited with code {outcome.exit_code}"
                )
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
                self._set_progress(job_id, stage="validating", process_alive=False)
                self.db.update_job(job_id, status="VALIDATING")
                result_text: str | None = None
                if request.result_format == "json":
                    value = validate_json_result(ctx.result_file, int(self.config.get("result_max_bytes")))
                else:
                    result_text = validate_text_result(ctx.result_file, int(self.config.get("result_max_bytes")))
                    value = None
                max_artifact_files = int(self.config.get("artifact_max_files", 200))
                max_artifact_bytes = int(self.config.get("artifact_max_total_bytes", 1024 * 1024 * 1024))
                materialized_artifacts: list[str] = []
                if value is not None:
                    materialized_artifacts = materialize_artifact_payloads(
                        value, ctx.artifact_dir, max_artifact_files, max_artifact_bytes
                    )
                declared_roles = normalize_declared_roles((value or {}).get("artifacts", []))
                target_workspace = paths.get("target")
                target_delta = calculate_delta(target_workspace) if target_workspace else None
                if (
                    target_workspace
                    and target_delta
                    and not target_delta.changed
                    and not target_delta.deleted
                    and request.profile != "analysis-only"
                ):
                    raise RelayError(
                        "TARGET_NOT_MODIFIED",
                        "The Agent did not create or modify anything in the requested Working folder.",
                    )
                if target_workspace and target_delta and (target_delta.changed or target_delta.deleted):
                    copy_delta_to_artifacts(target_workspace, target_delta, ctx.artifact_dir)
                artifact_records = scan_artifacts(
                    ctx.artifact_dir, max_artifact_files, max_artifact_bytes, declared_roles
                )
                if value is not None:
                    value = reconcile_json_artifacts(value, artifact_records)
                    ctx.result_file.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                    result_status = value["status"]
                else:
                    result_status = "complete"
                result_summary = self._resolve_result_summary(value, result_text)
                if result_status == "failed":
                    raise RelayError("PROCESS_CRASHED", f"{worker} returned status=failed", False)
                self.db.update_job(job_id, status="DELIVERING")
                self._set_progress(job_id, stage="delivering", process_alive=False)
                output_path = safe_resolve(Path(job["output_path"]))
                artifact_path = safe_resolve(Path(job["artifact_path"]))
                candidate_root = self.config.home / "review-candidates" / job_id if review_enabled else None
                delivery_output = candidate_root / output_path.name if candidate_root else output_path
                delivery_artifacts = candidate_root / "artifacts" if candidate_root else artifact_path
                if candidate_root:
                    ensure_dir(delivery_artifacts)
                atomic_deliver_pair(
                    ctx.result_file,
                    delivery_output,
                    ctx.artifact_dir,
                    delivery_artifacts,
                    overwrite=request.overwrite,
                )
                target_manifest = None
                if target_workspace and target_delta:
                    target_manifest = {
                        "target": str(target_workspace.target),
                        "existed": target_workspace.existed,
                        "baseline": target_workspace.baseline,
                        "delta": target_delta.to_dict(),
                    }
                    if candidate_root and (target_delta.changed or target_delta.deleted):
                        delta_root = candidate_root / "target-delta"
                        for relative in target_delta.changed:
                            source = ctx.artifact_dir / Path(relative)
                            destination = delta_root / Path(relative)
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, destination)
                if (
                    not review_enabled
                    and target_workspace
                    and target_delta
                    and (target_delta.changed or target_delta.deleted)
                ):
                    apply_delta(target_workspace, target_delta)
                self.db.add_artifact(
                    job_id,
                    relative_path=output_path.name,
                    final_path=str(delivery_output),
                    mime_type="application/json" if request.result_format == "json" else "text/plain",
                    size=delivery_output.stat().st_size,
                    sha256=sha256_file(delivery_output),
                    artifact_uid=new_artifact_uid(),
                    role="result",
                    producer_attempt_id=attempt_id,
                    publication_status="candidate" if review_enabled else "published",
                )
                for item in artifact_records:
                    self.db.add_artifact(
                        job_id,
                        relative_path=item["relative_path"],
                        final_path=str(delivery_artifacts / item["relative_path"]),
                        mime_type=item["mime_type"],
                        size=item["size"],
                        sha256=item["sha256"],
                        artifact_uid=new_artifact_uid(),
                        role=item.get("role") or "output",
                        producer_attempt_id=attempt_id,
                        publication_status="candidate" if review_enabled else "published",
                    )
                receipt = {
                    "ok": True,
                    "status": "partial" if result_status == "partial" else "completed",
                    "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
                    "job_id": job_id,
                    "task_run_id": job_id,
                    "run_id": job_id,
                    "task_summary": self._receipt_summary(job, "task_summary"),
                    "result_summary": result_summary if bool(job.get("replayable", 1)) else None,
                    "failure_reason": None,
                    "trigger_type": job.get("trigger_type", "manual"),
                    "task_snapshot": json.loads(job["task_snapshot_json"]) if job.get("task_snapshot_json") else None,
                    "task_inputs": dict(request.inputs or {}),
                    "worker": worker,
                    "result_path": str(delivery_output),
                    "artifact_path": str(delivery_artifacts),
                    "result_status": result_status,
                    "uncertainties_count": len(value.get("uncertainties", [])) if value else None,
                    "missing_items_count": len(value.get("missing_items", [])) if value else None,
                    "result_sha256": sha256_file(delivery_output),
                    "artifacts_count": len(artifact_records),
                    "materialized_artifacts_count": len(materialized_artifacts),
                    "target_path": str(target_workspace.target) if target_workspace else None,
                    "target_changes": target_delta.to_dict() if target_delta else None,
                    "attempted_workers": [e["worker"] for e in errors] + [worker],
                    "content_verified": False,
                    "content_verification_note": "Relay verifies delivery and format, not factual accuracy.",
                    "review_status": "pending_human" if review_enabled else "not_required",
                    "delivery_status": "deferred" if review_enabled else "delivered",
                }
                final_job_status = "PARTIAL" if result_status == "partial" else "COMPLETED"
                self.db.update_job(
                    job_id,
                    status=final_job_status,
                    result_status=result_status,
                    result_summary=result_summary,
                    actual_worker=worker,
                    receipt_json=json.dumps(receipt, ensure_ascii=False),
                    completed_at=utc_now(),
                    error_code=None,
                    error_message=None,
                    review_status="pending_human" if review_enabled else "not_required",
                    review_candidate_root=str(candidate_root) if candidate_root else None,
                    review_target_delta_json=json.dumps(target_manifest, ensure_ascii=False)
                    if target_manifest
                    else None,
                )
                self.db.update_attempt(
                    attempt_id,
                    status="SUCCEEDED",
                    completed_at=utc_now(),
                    exit_code=outcome.exit_code,
                )
                self.db.add_event(job_id, "JOB_COMPLETED", receipt)
                json_dump(delivery_output.parent / "relay-receipt.json", receipt)
                json_dump(
                    delivery_artifacts / "manifest.json",
                    {
                        "job_id": job_id,
                        "run_id": job_id,
                        "artifacts": [
                            {
                                **item,
                                "job_id": job_id,
                                "run_id": job_id,
                                "role": item.get("role") or "output",
                            }
                            for item in artifact_records
                        ],
                        "inputs": input_manifest,
                    },
                )
                self.db.scrub_non_replayable(job_id)
                if not review_enabled:
                    self._refresh_search_index(job_id)
                else:
                    from .reviews.service import ReviewService

                    ReviewService(self.db, self, self.config).create_task_review(
                        job_id,
                        reviewer="human",
                        max_reruns=0,
                        review_id=request.review_id,
                    )
                self._clear_progress(job_id)
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

    def _refresh_search_index(self, job_id: str) -> None:
        try:
            self.db.index_run(job_id)
        except Exception:  # pragma: no cover - search must not break execution
            logger.warning("Could not index Task Run %s", job_id, exc_info=True)
        for artifact in self.db.artifacts_for_job(job_id):
            artifact_uid = artifact.get("artifact_uid")
            if not artifact_uid:
                continue
            try:
                self.db.index_artifact(artifact_uid)
            except Exception:  # pragma: no cover - search must not break execution
                logger.warning("Could not index Artifact %s", artifact_uid, exc_info=True)

    def _fail_job(self, job_id: str, code: str, message: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
        attempt_rows = self.db.attempts_for_job(job_id)
        log_paths = [
            {"worker": row.get("worker"), "stdout": row.get("stdout_path"), "stderr": row.get("stderr_path")}
            for row in attempt_rows
        ]
        receipt = {
            "ok": False,
            "status": "failed",
            "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
            "job_id": job_id,
            "task_run_id": job_id,
            "task_summary": self._receipt_summary(self.db.get_job(job_id) or {}, "task_summary"),
            "result_summary": None,
            "failure_reason": normalize_summary(
                message, max_chars=1000, field="failure_reason", error_code="INTERNAL_ERROR"
            ),
            "error_code": code,
            "error_message": message,
            "attempts": errors,
            "logs": log_paths,
            "content_verified": False,
            "task_inputs": self._stored_task_inputs(self.db.get_job(job_id) or {}),
        }
        self.db.update_job(
            job_id,
            status="FAILED",
            error_code=code,
            error_message=message,
            result_summary=None,
            receipt_json=json.dumps(receipt, ensure_ascii=False),
            completed_at=utc_now(),
        )
        self.db.add_event(job_id, "JOB_FAILED", receipt)
        self.db.scrub_non_replayable(job_id)
        self._refresh_search_index(job_id)
        self._clear_progress(job_id)
        return receipt

    def run(
        self,
        request: JobRequest,
        submitted_via: str | None = None,
        *,
        trigger_type: str | None = None,
    ) -> dict[str, Any]:
        job, reused = self.create_job(
            request,
            queued=False,
            submitted_via=submitted_via,
            trigger_type=trigger_type,
        )
        if reused:
            receipt = self.receipt(job["job_id"])
            receipt["deduplicated"] = True
            return receipt
        return self.execute_job(job["job_id"])

    def queue(
        self,
        request: JobRequest,
        submitted_via: str | None = None,
        *,
        trigger_type: str | None = None,
    ) -> dict[str, Any]:
        job, reused = self.create_job(
            request,
            queued=True,
            submitted_via=submitted_via,
            trigger_type=trigger_type,
        )
        return {
            "ok": True,
            "status": "reused" if reused else "queued",
            "job_id": job["job_id"],
            "task_run_id": job["job_id"],
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
            "task_run_id": job["job_id"],
            "deduplicated": reused,
        }

    def receipt(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
        if job.get("receipt_json"):
            try:
                receipt = json.loads(job["receipt_json"])
                if isinstance(receipt, dict):
                    receipt.setdefault("task_inputs", self._stored_task_inputs(job))
                return receipt
            except json.JSONDecodeError:
                pass
        schema_version = int(job.get("receipt_schema_version") or 1)
        receipt = {
            "ok": job["status"] not in {"FAILED", "CANCELLED"},
            "status": job["status"].lower(),
            "receipt_schema_version": schema_version,
            "job_id": job_id,
            "task_run_id": job_id,
            "run_id": job_id,
            "trigger_type": job.get("trigger_type", "manual"),
            "worker": job.get("actual_worker"),
            "result_path": job.get("output_path"),
            "artifact_path": job.get("artifact_path"),
            "error_code": job.get("error_code"),
            "error_message": job.get("error_message"),
            "task_inputs": self._stored_task_inputs(job),
        }
        if schema_version >= 2:
            receipt.update(
                {
                    "task_summary": self._receipt_summary(job, "task_summary"),
                    "result_summary": self._receipt_summary(job, "result_summary"),
                    "failure_reason": self._resolve_failure_reason(job),
                }
            )
        return receipt

    @staticmethod
    def _stored_task_inputs(job: dict[str, Any]) -> dict[str, Any]:
        try:
            snapshot = json.loads(job.get("task_snapshot_json") or "{}")
            if isinstance(snapshot, dict) and isinstance(snapshot.get("inputs"), dict):
                return snapshot["inputs"]
        except json.JSONDecodeError:
            pass
        try:
            request = json.loads(job.get("request_json") or "{}")
            if isinstance(request, dict) and isinstance(request.get("inputs"), dict):
                return request["inputs"]
        except json.JSONDecodeError:
            pass
        return {}

    def show(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
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
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
        if not bool(job.get("replayable", 1)) or job.get("request_json") in (None, "", "{}"):
            raise RelayError("JOB_NOT_REPLAYABLE", "This Task Run did not save a replayable request.")
        request = JobRequest.from_dict(json.loads(job["request_json"]))
        request.request_id = None
        request.force_new = force_new
        request.output_path = None
        request.artifact_path = None
        return self.run(request, submitted_via="gui", trigger_type="rerun")

    def queue_rerun(self, job_id: str, submitted_via: str = "gui") -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
        if not bool(job.get("replayable", 1)) or job.get("request_json") in (None, "", "{}"):
            raise RelayError("JOB_NOT_REPLAYABLE", "This Task Run did not save a replayable request.")
        request = JobRequest.from_dict(json.loads(job["request_json"]))
        request.request_id = None
        request.force_new = True
        request.output_path = None
        request.artifact_path = None
        request.caller = "human"
        result = self.queue(request, submitted_via=submitted_via, trigger_type="rerun")
        result["source_job_id"] = job_id
        return result

    def create_task(self, spec: TaskSpec) -> dict[str, Any]:
        row = spec.to_row()
        self.db.create_task(row)
        return self.db.get_task(row["task_id"])

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if not task:
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        normalized = TaskSpec.normalize_changes(changes)
        if not normalized:
            return task
        self.db.update_task(task_id, **normalized)
        return self.db.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        if not self.db.get_task(task_id):
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        return self.db.delete_task(task_id)

    def run_task(
        self,
        task_id: str,
        *,
        request: JobRequest | None = None,
        queued: bool = False,
        submitted_via: str | None = None,
        trigger_type: str | None = None,
        routine_id: str | None = None,
        caller: str = "human",
    ) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        task = self.db.get_task(task_id)
        if not task:
            raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
        instructions = task.get("instructions") or ""
        base = JobRequest(
            task=instructions,
            worker=task.get("default_worker") or "auto",
            model=task.get("default_model"),
            fallback=bool(task.get("fallback_enabled", 1)) if task.get("fallback_enabled") is not None else None,
            timeout_seconds=task.get("timeout_seconds"),
            profile=task.get("profile") or "web-research",
            result_format=task.get("result_format") or "json",
            caller=caller,
        )
        if request:
            base.task = request.task or instructions
            base.worker = request.worker or base.worker
            base.model = request.model or base.model
            base.result_format = request.result_format or base.result_format
            base.profile = request.profile or base.profile
            base.timeout_seconds = request.timeout_seconds or base.timeout_seconds
            base.fallback = request.fallback if request.fallback is not None else base.fallback
            base.attachments = list(request.attachments)
            base.artifact_inputs = list(request.artifact_inputs)
            base.inputs = dict(request.inputs or {})
            base.request_id = request.request_id
            base.output_path = request.output_path
            base.artifact_path = request.artifact_path
            base.caller = request.caller
        definition = {
            "task_id": task["task_id"],
            "name": task["name"],
            "version": task["version"],
            "instructions": instructions,
            "default_worker": task.get("default_worker"),
            "default_model": task.get("default_model"),
            "fallback_enabled": task.get("fallback_enabled"),
            "timeout_seconds": task.get("timeout_seconds"),
            "profile": task.get("profile"),
            "result_format": task.get("result_format"),
            "input_schema": task.get("input_schema"),
            "output_contract": task.get("output_contract"),
            "validation_policy": task.get("validation_policy"),
            "task_summary": task.get("task_summary"),
            "review_policy": _decode_json_object(task.get("review_policy_json")),
        }
        job, reused = self.create_job(
            base,
            queued=queued,
            submitted_via=submitted_via,
            task_id=task["task_id"],
            task_definition=definition,
            trigger_type=trigger_type,
        )
        if routine_id:
            self.db.update_job(job["job_id"], routine_id=routine_id)
            job["routine_id"] = routine_id
        return job, reused, task

    def load_task_for_snapshot(self, task_id: str) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if not task:
            raise RelayError("PROJECT_TASK_MISSING", f"Task not found: {task_id}")
        return {
            "task_id": task["task_id"],
            "name": task["name"],
            "version": task["version"],
            "instructions": task.get("instructions") or "",
            "description": task.get("description"),
            "default_worker": task.get("default_worker"),
            "default_model": task.get("default_model"),
            "fallback_enabled": task.get("fallback_enabled"),
            "timeout_seconds": task.get("timeout_seconds"),
            "profile": task.get("profile"),
            "result_format": task.get("result_format"),
            "input_schema": task.get("input_schema"),
            "output_contract": task.get("output_contract"),
            "validation_policy": task.get("validation_policy"),
            "task_summary": task.get("task_summary"),
        }

    def run_task_from_snapshot(
        self,
        task_snapshot: dict[str, Any],
        *,
        request: JobRequest | None = None,
        queued: bool = False,
        submitted_via: str | None = None,
        caller: str = "human",
    ) -> tuple[dict[str, Any], bool]:
        instructions = task_snapshot.get("instructions") or ""
        base = JobRequest(
            task=instructions,
            worker=task_snapshot.get("default_worker") or "auto",
            model=task_snapshot.get("default_model"),
            fallback=bool(task_snapshot.get("fallback_enabled", 1))
            if task_snapshot.get("fallback_enabled") is not None
            else None,
            timeout_seconds=task_snapshot.get("timeout_seconds"),
            profile=task_snapshot.get("profile") or "web-research",
            result_format=task_snapshot.get("result_format") or "json",
            caller=caller,
        )
        if request:
            base.task = request.task or instructions
            base.worker = request.worker or base.worker
            base.model = request.model or base.model
            base.result_format = request.result_format or base.result_format
            base.profile = request.profile or base.profile
            base.timeout_seconds = request.timeout_seconds or base.timeout_seconds
            base.fallback = request.fallback if request.fallback is not None else base.fallback
            base.attachments = list(request.attachments)
            base.artifact_inputs = list(request.artifact_inputs)
            base.inputs = dict(request.inputs or {})
            base.request_id = request.request_id
            base.output_path = request.output_path
            base.artifact_path = request.artifact_path
            base.caller = request.caller
        definition = {
            "task_id": task_snapshot["task_id"],
            "name": task_snapshot.get("name"),
            "version": task_snapshot.get("version"),
            "instructions": instructions,
            "default_worker": task_snapshot.get("default_worker"),
            "default_model": task_snapshot.get("default_model"),
            "fallback_enabled": task_snapshot.get("fallback_enabled"),
            "timeout_seconds": task_snapshot.get("timeout_seconds"),
            "profile": task_snapshot.get("profile"),
            "result_format": task_snapshot.get("result_format"),
            "input_schema": task_snapshot.get("input_schema"),
            "output_contract": task_snapshot.get("output_contract"),
            "validation_policy": task_snapshot.get("validation_policy"),
            "task_summary": task_snapshot.get("task_summary"),
            "review_policy": task_snapshot.get("review_policy"),
        }
        return self.create_job(
            base,
            queued=queued,
            submitted_via=submitted_via,
            task_id=task_snapshot["task_id"],
            task_definition=definition,
            trigger_type="project" if caller == "service" else None,
        )

    def save_run_as_task(self, run_id: str, *, name: str, description: str | None = None) -> dict[str, Any]:
        job = self.db.get_job(run_id)
        if not job:
            raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {run_id}")
        snapshot: dict[str, Any] = {}
        if job.get("task_snapshot_json"):
            try:
                snapshot = json.loads(job["task_snapshot_json"])
            except json.JSONDecodeError:
                snapshot = {}
        request: dict[str, Any] = {}
        if job.get("request_json"):
            try:
                request = json.loads(job["request_json"])
            except json.JSONDecodeError:
                request = {}
        spec = TaskSpec.from_snapshot(snapshot, request, name=name, description=description)
        return self.create_task(spec)
