from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any

from .db import Database
from .errors import RelayError
from .progress import diagnose_progress
from .schedules.snapshots import validate_source_job
from .search import normalize_limit, normalize_max_bytes, result_summary, snippet

RESULT_STATUS = {
    "completed": "COMPLETED",
    "partial": "PARTIAL",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
}


def _encode_cursor(value: tuple[str, str]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(decoded, list) or len(decoded) != 2 or not all(isinstance(item, str) for item in decoded):
            raise ValueError
        return decoded[0], decoded[1]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise RelayError("INVALID_REQUEST", "The cursor is invalid.") from None


def _summary(job: dict[str, Any], *, hide_task: bool) -> dict[str, Any]:
    request: dict[str, Any] = {}
    try:
        if job.get("request_json"):
            value = json.loads(job["request_json"])
            if isinstance(value, dict):
                request = value
    except json.JSONDecodeError:
        pass
    job.pop("request_json", None)
    if hide_task:
        job.pop("task_text", None)
        job.pop("task_preview", None)
    job["model"] = request.get("model")
    return job


def list_jobs(
    db: Database,
    *,
    bucket: str = "all",
    status: str | None = None,
    agent: str | None = None,
    submitted_via: str | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    hide_task: bool = True,
) -> dict[str, Any]:
    if status:
        status = RESULT_STATUS.get(status.lower(), status.upper())
    rows = db.list_jobs_page(
        bucket=bucket,
        status=status,
        agent=agent,
        submitted_via=submitted_via,
        query=query,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=_decode_cursor(cursor),
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        sort_value = {
            "waiting": rows[-1]["created_at"],
            "running": rows[-1].get("started_at") or rows[-1]["created_at"],
            "active": rows[-1]["created_at"],
            "finished": rows[-1].get("completed_at") or rows[-1]["created_at"],
            "all": rows[-1]["created_at"],
        }[bucket]
        next_cursor = _encode_cursor((sort_value, rows[-1]["job_id"]))
    summaries = [_summary(row, hide_task=hide_task) for row in rows]
    for row in summaries:
        row["run_id"] = row["job_id"]
        row.setdefault("trigger_type", "manual")
    return {
        "ok": True,
        "jobs": summaries,
        "runs": summaries,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def _load_request(job: dict[str, Any]) -> dict[str, Any]:
    value = job.get("request_json")
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _task_preview(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if len(text) <= 200:
        return text
    return f"{text[:100]} … {text[-100:]}"


def job_detail(engine, job_id: str) -> dict[str, Any]:
    raw = engine.db.get_job(job_id)
    if not raw:
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    detail = engine.show(job_id)
    request = _load_request(raw)
    safe_request = {
        key: request[key]
        for key in (
            "worker",
            "fallback",
            "fallback_agents",
            "result_format",
            "profile",
            "timeout_seconds",
            "workspace",
            "target_path",
            "overwrite",
            "force_new",
            "model",
            "request_id",
        )
        if key in request
    }
    if engine._history_display_mode() == "full":
        for key in ("task", "task_file", "attachments"):
            if key in request:
                safe_request[key] = request[key]
    detail["request"] = safe_request
    detail["task_preview"] = _task_preview(request.get("task") or raw.get("task_text") or raw.get("task_preview"))
    status = raw.get("status")
    can_schedule = False
    schedule_reason: str | None = None
    if status == "COMPLETED" and raw.get("result_status") == "complete":
        try:
            validate_source_job(raw, engine.agent_registry)
            can_schedule = True
        except RelayError as exc:
            schedule_reason = exc.code
    else:
        schedule_reason = "SCHEDULE_NOT_ELIGIBLE"
    detail["actions"] = {
        "can_cancel": status in {"QUEUED", "PREPARING", "RUNNING", "VALIDATING", "DELIVERING"},
        "can_check_progress": status
        in {
            "QUEUED",
            "PREPARING",
            "RUNNING",
            "VALIDATING",
            "DELIVERING",
            "CANCEL_REQUESTED",
        },
        "can_rerun": (
            status in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
            and bool(raw.get("replayable", 1))
            and raw.get("request_json") not in (None, "", "{}")
        ),
        "can_copy": bool(raw.get("replayable", 1)) or bool(detail.get("task_text") or detail.get("task_preview")),
        "can_schedule": can_schedule,
        "schedule_reason": schedule_reason,
        "schedule_requires_isolation": not bool(engine.config.get("service_isolation_acknowledged", False)),
        "can_open_result": bool(detail.get("output_path") and Path(detail["output_path"]).is_file()),
        "can_open_folder": bool(detail.get("artifact_path") and Path(detail["artifact_path"]).is_dir()),
    }
    return detail


def job_result(db: Database, job_id: str, *, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    path = Path(job["output_path"])
    if not path.is_file():
        return {"ok": True, "job_id": job_id, "available": False, "path": str(path)}
    raw = path.read_bytes()
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    payload: dict[str, Any] = {
        "ok": True,
        "job_id": job_id,
        "available": True,
        "path": str(path),
        "format": job.get("format"),
        "size": len(raw),
        "truncated": truncated,
        "text": text,
    }
    if job.get("format") == "json" and not truncated:
        try:
            payload["data"] = json.loads(text)
        except json.JSONDecodeError:
            payload["data"] = None
    return payload


def job_artifacts(db: Database, job_id: str) -> dict[str, Any]:
    if not db.get_job(job_id):
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    return {"ok": True, "job_id": job_id, "artifacts": db.artifacts_for_job(job_id)}


def job_events(db: Database, job_id: str) -> dict[str, Any]:
    if not db.get_job(job_id):
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    return {"ok": True, "job_id": job_id, "events": db.events_for_job(job_id)}


def check_job_progress(engine, job_id: str) -> dict[str, Any]:
    job = engine.db.get_job(job_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    result = diagnose_progress(
        job,
        engine.progress_for_job(job_id),
        engine.db.attempts_for_job(job_id),
    )
    engine.db.add_event(job_id, "PROGRESS_CHECKED", result)
    return result


def job_logs(
    db: Database,
    job_id: str,
    *,
    attempt_id: int,
    stream: str,
    offset: int | None = None,
    limit: int = 16000,
    errors_only: bool = False,
) -> dict[str, Any]:
    if not db.get_job(job_id):
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {job_id}")
    attempts = {int(row["attempt_id"]): row for row in db.attempts_for_job(job_id)}
    attempt = attempts.get(attempt_id)
    if not attempt:
        raise RelayError("INVALID_REQUEST", "The attempt does not belong to this job.")
    if stream not in {"stdout", "stderr"}:
        raise RelayError("INVALID_REQUEST", "The log stream must be stdout or stderr.")
    if limit < 1 or limit > 65536:
        raise RelayError("INVALID_REQUEST", "The log limit must be between 1 and 65536 bytes.")
    path_value = attempt.get(f"{stream}_path")
    if not path_value:
        return {
            "ok": True,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "stream": stream,
            "text": "",
            "next_offset": 0,
            "eof": True,
            "reset": False,
        }
    path = Path(path_value)
    if not path.is_file():
        return {
            "ok": True,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "stream": stream,
            "text": "",
            "next_offset": 0,
            "eof": True,
            "reset": False,
        }
    size = path.stat().st_size
    reset = offset is not None and offset > size
    start = max(size - limit, 0) if offset is None or reset else max(offset, 0)
    with path.open("rb") as handle:
        handle.seek(start)
        chunk = handle.read(limit)
    next_offset = start + len(chunk)
    text = chunk.decode("utf-8", errors="replace")
    if errors_only:
        error_markers = ("error", "fail", "exception", "traceback")
        text = "\n".join(
            line for line in text.splitlines() if any(marker in line.casefold() for marker in error_markers)
        )
    return {
        "ok": True,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "stream": stream,
        "text": text,
        "path": str(path),
        "start_offset": start,
        "next_offset": next_offset,
        "eof": next_offset >= size,
        "reset": reset,
    }


def list_agents(engine) -> dict[str, Any]:
    return {"ok": True, "agents": engine.agent_registry.list_agents()}


def run_detail(engine, run_id: str) -> dict[str, Any]:
    detail = job_detail(engine, run_id)
    detail["run_id"] = detail["job_id"]
    return detail


def run_result(db: Database, run_id: str, *, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
    payload = job_result(db, run_id, max_bytes=max_bytes)
    payload["run_id"] = run_id
    return payload


def run_artifacts(db: Database, run_id: str) -> dict[str, Any]:
    payload = job_artifacts(db, run_id)
    payload["run_id"] = run_id
    return payload


def run_events(db: Database, run_id: str) -> dict[str, Any]:
    payload = job_events(db, run_id)
    payload["run_id"] = run_id
    return payload


def run_lineage(db: Database, run_id: str) -> dict[str, Any]:
    job = db.get_job(run_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Job not found: {run_id}")
    return {
        "ok": True,
        "job_id": run_id,
        "run_id": run_id,
        "inputs": db.lineage_for_job(run_id),
        "outputs": db.artifacts_for_job(run_id),
    }


def artifact_detail(db: Database, artifact_uid: str) -> dict[str, Any]:
    artifact = db.artifact_by_uid(artifact_uid)
    if not artifact:
        raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {artifact_uid}")
    return {"ok": True, "artifact": artifact}


def artifact_lineage(db: Database, artifact_uid: str) -> dict[str, Any]:
    artifact = db.artifact_by_uid(artifact_uid)
    if not artifact:
        raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {artifact_uid}")
    return {"ok": True, "artifact": artifact, "consumers": db.lineage_for_artifact(artifact_uid)}


def search_runs(db: Database, **kwargs: Any) -> dict[str, Any]:
    limit = normalize_limit(kwargs.pop("limit", 20))
    offset = int(kwargs.pop("offset", 0) or 0)
    rows = db.search_runs(limit=limit, offset=offset, **kwargs)
    items: list[dict[str, Any]] = []
    for row in rows:
        artifacts = db.artifacts_for_job(row["job_id"])
        summary = result_summary(row) if "result_summary" not in row else row.get("result_summary")
        items.append(
            {
                "run_id": row["job_id"],
                "job_id": row["job_id"],
                "title": row.get("title"),
                "status": row.get("status"),
                "result_status": row.get("result_status"),
                "executed_at": row.get("completed_at") or row.get("created_at"),
                "worker": row.get("actual_worker") or row.get("requested_worker"),
                "trigger_type": row.get("trigger_type") or "manual",
                "summary": summary,
                "artifact_count": len(artifacts),
                "artifact_roles": sorted({item.get("role") or "output" for item in artifacts}),
                "artifacts_available": all(Path(str(item.get("final_path") or "")).is_file() for item in artifacts),
                "relevance": float(row.get("relevance") or 0.0),
            }
        )
    return {"ok": True, "kind": "runs", "items": items, "next_cursor": None, "has_more": len(items) == limit}


def search_artifacts(db: Database, **kwargs: Any) -> dict[str, Any]:
    limit = normalize_limit(kwargs.pop("limit", 20))
    offset = int(kwargs.pop("offset", 0) or 0)
    rows = db.search_artifacts(limit=limit, offset=offset, **kwargs)
    items = []
    for row in rows:
        path = Path(str(row.get("final_path") or ""))
        content = None
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
        items.append(
            {
                "artifact_uid": row.get("artifact_uid"),
                "run_id": row.get("job_id"),
                "name": row.get("relative_path"),
                "role": row.get("role") or "output",
                "mime_type": row.get("mime_type"),
                "size": row.get("size"),
                "sha256": row.get("sha256"),
                "available": path.is_file(),
                "snippet": snippet(content),
                "relevance": float(row.get("relevance") or 0.0),
            }
        )
    return {"ok": True, "kind": "artifacts", "items": items, "next_cursor": None, "has_more": len(items) == limit}


def artifact_content(db: Database, artifact_uid: str, *, max_bytes: int = 65536) -> dict[str, Any]:
    return db.artifact_content(artifact_uid, normalize_max_bytes(max_bytes))


def run_logs(
    db: Database,
    run_id: str,
    *,
    attempt_id: int,
    stream: str,
    offset: int | None = None,
    limit: int = 16000,
    errors_only: bool = False,
) -> dict[str, Any]:
    payload = job_logs(
        db,
        run_id,
        attempt_id=attempt_id,
        stream=stream,
        offset=offset,
        limit=limit,
        errors_only=errors_only,
    )
    payload["run_id"] = run_id
    return payload


def list_runs(db: Database, **kwargs: Any) -> dict[str, Any]:
    payload = list_jobs(db, **kwargs)
    payload["runs"] = payload["jobs"]
    return payload


def run_progress(engine, run_id: str) -> dict[str, Any]:
    payload = check_job_progress(engine, run_id)
    payload["run_id"] = run_id
    return payload


def get_agent(engine, agent_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, "agent": engine.agent_registry.get_definition(agent_id)}
    except KeyError:
        raise RelayError("INVALID_REQUEST", f"Unknown agent: {agent_id}") from None


def _task_public(task: dict[str, Any]) -> dict[str, Any]:
    return {**task, "fallback_enabled": bool(task.get("fallback_enabled", 1))}


def list_tasks(engine) -> dict[str, Any]:
    return {"ok": True, "tasks": [_task_public(t) for t in engine.db.list_tasks(limit=200)]}


def create_task(engine, payload: dict[str, Any]) -> dict[str, Any]:
    from .models import TaskSpec

    spec = TaskSpec(
        name=str(payload.get("name") or "").strip(),
        instructions=payload.get("instructions") or payload.get("task") or "",
        description=payload.get("description"),
        default_worker=payload.get("default_worker") or payload.get("worker"),
        fallback_enabled=bool(payload.get("fallback_enabled", True)),
        timeout_seconds=payload.get("timeout_seconds"),
        profile=payload.get("profile"),
        result_format=payload.get("result_format") or payload.get("format"),
        input_schema=payload.get("input_schema"),
        output_contract=payload.get("output_contract"),
        validation_policy=payload.get("validation_policy"),
    )
    task = engine.create_task(spec)
    return {"ok": True, "task": _task_public(task)}


def get_task(engine, task_id: str) -> dict[str, Any]:
    task = engine.db.get_task(task_id)
    if not task:
        raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
    return {"ok": True, "task": _task_public(task)}


def update_task(engine, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    task = engine.update_task(task_id, **payload)
    return {"ok": True, "task": _task_public(task)}


def delete_task(engine, task_id: str) -> dict[str, Any]:
    engine.delete_task(task_id)
    return {"ok": True, "task_id": task_id, "deleted": True}


def run_task(engine, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .models import JobRequest

    overrides = payload.get("request") or {}
    request = None
    if overrides or payload.get("worker") or payload.get("format"):
        request = JobRequest(
            task=overrides.get("task") or "",
            worker=overrides.get("worker") or payload.get("worker") or "auto",
            result_format=overrides.get("result_format") or payload.get("format") or "json",
            profile=overrides.get("profile"),
            timeout_seconds=overrides.get("timeout_seconds"),
            attachments=list(overrides.get("attachments") or []),
            artifact_inputs=list(overrides.get("artifact_inputs") or []),
            request_id=overrides.get("request_id"),
            caller=overrides.get("caller", "human"),
        )
    job, reused, task = engine.run_task(
        task_id,
        request=request,
        queued=bool(payload.get("queued", False)),
        submitted_via=payload.get("submitted_via"),
    )
    return {"ok": True, "run": job, "reused": reused, "task": _task_public(task)}


def runs_for_task(engine, task_id: str, *, limit: int = 50) -> dict[str, Any]:
    if not engine.db.get_task(task_id):
        raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
    return {"ok": True, "task_id": task_id, "runs": engine.db.runs_for_task(task_id, limit=limit)}


def save_run_as_task(engine, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    task = engine.save_run_as_task(
        run_id,
        name=str(payload.get("name") or "").strip(),
        description=payload.get("description"),
    )
    return {"ok": True, "task": _task_public(task)}


def _project_public(project: dict[str, Any]) -> dict[str, Any]:
    return {**project, "deleted_at": project.get("deleted_at")}


def _project_run_public(run: dict[str, Any], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {**run}
    if snapshot is None and run.get("project_snapshot_json"):
        try:
            snapshot = json.loads(run["project_snapshot_json"])
        except Exception:
            snapshot = None
    if snapshot:
        out["snapshot"] = snapshot
    return out


def _step_public(step: dict[str, Any]) -> dict[str, Any]:
    return {**step}


def list_projects(engine) -> dict[str, Any]:
    return {"ok": True, "projects": [_project_public(p) for p in engine.project_service.list_projects(limit=200)]}


def create_project(engine, payload: dict[str, Any]) -> dict[str, Any]:
    project = engine.project_service.create_project(payload)
    return {"ok": True, "project": _project_public(project)}


def get_project(engine, project_id: str) -> dict[str, Any]:
    project = engine.project_service.get_project(project_id)
    return {"ok": True, "project": _project_public(project)}


def update_project(engine, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    project = engine.project_service.update_project(project_id, payload)
    return {"ok": True, "project": _project_public(project)}


def delete_project(engine, project_id: str) -> dict[str, Any]:
    engine.project_service.soft_delete_project(project_id)
    return {"ok": True, "project_id": project_id, "deleted": True}


def run_project(engine, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    inputs = payload.get("inputs") or []
    project_run = engine.project_service.create_project_run(project_id, external_inputs=inputs)
    return {
        "ok": True,
        "project_run": _project_run_public(project_run["project_run"], None),
        "project_run_id": project_run["project_run_id"],
        "steps": [_step_public(s) for s in project_run["steps"]],
    }


def project_runs(engine, project_id: str) -> dict[str, Any]:
    rows = engine.db.list_project_runs(project_id=project_id, limit=50)
    return {
        "ok": True,
        "project_id": project_id,
        "project_runs": [
            _project_run_public(r, json.loads(r["project_snapshot_json"]) if r.get("project_snapshot_json") else None)
            for r in rows
        ],
    }


def project_run(engine, project_run_id: str) -> dict[str, Any]:
    run = engine.db.get_project_run(project_run_id)
    if not run:
        raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
    return {
        "ok": True,
        "project_run": _project_run_public(
            run, json.loads(run["project_snapshot_json"]) if run.get("project_snapshot_json") else None
        ),
    }


def project_run_steps(engine, project_run_id: str) -> dict[str, Any]:
    steps = engine.db.list_project_steps(project_run_id)
    return {"ok": True, "project_run_id": project_run_id, "steps": [_step_public(s) for s in steps]}


def project_run_receipt(engine, project_run_id: str) -> dict[str, Any]:
    receipt = engine.project_service.project_run_receipt(project_run_id)
    return {"ok": True, "receipt": receipt}


def project_run_retry(engine, project_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    res = engine.project_service.retry_project_run(
        project_run_id,
        from_node=payload.get("from_node"),
        worker=payload.get("worker"),
    )
    return {"ok": True, "project_run": _project_run_public(res["project_run"], None), "target_node": res["target_node"]}


def project_run_cancel(engine, project_run_id: str) -> dict[str, Any]:
    run = engine.project_service.cancel_project_run(project_run_id)
    return {
        "ok": True,
        "project_run": _project_run_public(
            run, json.loads(run["project_snapshot_json"]) if run.get("project_snapshot_json") else None
        ),
    }


def _routine_public(routine):
    out = {**routine, "enabled": bool(routine.get("enabled", 1))}
    return out


def list_routines(engine, *, name=None, limit=200):
    return {
        "ok": True,
        "routines": [_routine_public(r) for r in engine.routine_service.list_routines(name=name, limit=limit)],
    }


def create_routine(engine, payload):
    routine = engine.routine_service.create_routine(payload)
    return {"ok": True, "routine": _routine_public(routine)}


def get_routine(engine, routine_id):
    routine = engine.routine_service.get_routine(routine_id)
    return {"ok": True, "routine": _routine_public(routine)}


def update_routine(engine, routine_id, payload):
    routine = engine.routine_service.update_routine(routine_id, payload)
    return {"ok": True, "routine": _routine_public(routine)}


def delete_routine(engine, routine_id):
    engine.routine_service.soft_delete_routine(routine_id)
    return {"ok": True, "routine_id": routine_id, "deleted": True}


def run_routine_now(engine, routine_id):
    run = engine.routine_service.run_now(routine_id)
    return {"ok": True, "run": run}


def routine_runs(engine, routine_id):
    if not engine.db.get_routine(routine_id):
        raise RelayError("ROUTINE_NOT_FOUND", f"Routine not found: {routine_id}")
    return {"ok": True, "routine_id": routine_id, "runs": engine.db.list_routine_runs(routine_id=routine_id, limit=100)}


def routine_receipt(engine, routine_id):
    receipt = engine.routine_service.routine_receipt(routine_id)
    return {"ok": True, "receipt": receipt}


def preview_routine(engine, payload):
    result = engine.routine_service.preview(payload)
    return {"ok": True, **result}


# --- Phase 6a Approvals ---


def list_approvals(engine, project_run_id: str) -> dict[str, Any]:
    from .approvals.service import ApprovalService

    service = ApprovalService(engine.db, engine, engine.config)
    approvals = service.db.list_approvals(project_run_id)
    return {"ok": True, "project_run_id": project_run_id, "approvals": approvals}


def get_approval(engine, token: str) -> dict[str, Any]:
    from .approvals.service import ApprovalService

    service = ApprovalService(engine.db, engine, engine.config)
    app = service.db.get_approval(token)
    if not app:
        raise RelayError("APPROVAL_NOT_FOUND", f"Approval not found for token: {token}")
    return {"ok": True, "approval": app}


def approve_checkpoint(engine, project_run_id: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .approvals.service import ApprovalService

    service = ApprovalService(engine.db, engine, engine.config)
    reviewer = str(payload.get("reviewer") or "human")
    res = service.approve(project_run_id, token, reviewer=reviewer)
    return {"ok": True, **res}


def reject_checkpoint(engine, project_run_id: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .approvals.service import ApprovalService

    service = ApprovalService(engine.db, engine, engine.config)
    reviewer = str(payload.get("reviewer") or "human")
    reason = str(payload.get("reason") or "")
    res = service.reject(project_run_id, token, reviewer=reviewer, reason=reason)
    return {"ok": True, **res}


def edit_checkpoint(engine, project_run_id: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .approvals.service import ApprovalService

    service = ApprovalService(engine.db, engine, engine.config)
    reviewer = str(payload.get("reviewer") or "human")
    edit_file_path = str(payload.get("file") or payload.get("edit_file_path") or "")
    role = str(payload.get("role") or "output")
    res = service.approve_with_edits(project_run_id, token, reviewer=reviewer, edit_file_path=edit_file_path, role=role)
    return {"ok": True, **res}
