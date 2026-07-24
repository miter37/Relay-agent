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
    return {
        "ok": True,
        "jobs": [_summary(row, hide_task=hide_task) for row in rows],
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


def get_agent(engine, agent_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, "agent": engine.agent_registry.get_definition(agent_id)}
    except KeyError:
        raise RelayError("INVALID_REQUEST", f"Unknown agent: {agent_id}") from None
