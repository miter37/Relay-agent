from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Any

from .db import Database
from .errors import RelayError
from .progress import diagnose_progress
from .receipts import RECEIPT_SCHEMA_VERSION
from .schedules.snapshots import validate_source_job
from .search import normalize_limit, normalize_max_bytes, result_summary, snippet
from .validation import normalize_summary

RESULT_STATUS = {
    "completed": "COMPLETED",
    "partial": "PARTIAL",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
}

CATALOG_SCHEMA_VERSION = 1


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


def _decode_catalog_cursor(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(decoded, list) or len(decoded) != 2 or not all(isinstance(item, str) for item in decoded):
            raise ValueError
        if not decoded[0] or not decoded[1]:
            raise ValueError
        return decoded[0], decoded[1]
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error):
        raise RelayError("INVALID_CURSOR", "The catalog cursor is invalid.") from None


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
    if job.get("job_id"):
        job["task_run_id"] = job["job_id"]
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
        row["task_run_id"] = row["job_id"]
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
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
    detail = engine.show(job_id)
    detail["artifacts"] = [
        item for item in detail.get("artifacts", []) if item.get("publication_status") in (None, "published")
    ]
    request = _load_request(raw)
    snapshot: dict[str, Any] = {}
    try:
        if raw.get("task_snapshot_json"):
            value = json.loads(raw["task_snapshot_json"])
            if isinstance(value, dict):
                snapshot = value
    except json.JSONDecodeError:
        pass
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
            "inputs",
        )
        if key in request
    }
    if engine._history_display_mode() == "full":
        for key in ("task", "task_file", "attachments"):
            if key in request:
                safe_request[key] = request[key]
    detail["request"] = safe_request
    snapshot_inputs = snapshot.get("inputs")
    detail["task_inputs"] = snapshot_inputs if isinstance(snapshot_inputs, dict) else request.get("inputs") or {}
    detail["task_input_schema"] = (snapshot.get("task_definition") or {}).get("input_schema")
    detail["input_integrity_warning"] = (
        "This historical Task Run did not preserve its Task input values."
        if detail.get("task_id") and not detail["task_inputs"] and detail.get("task_input_schema")
        else None
    )
    detail["task_preview"] = _task_preview(request.get("task") or raw.get("task_text") or raw.get("task_preview"))
    status = raw.get("status")
    can_schedule = False
    schedule_reason: str | None = None
    if (
        status == "COMPLETED"
        and raw.get("result_status") == "complete"
        and raw.get("review_status")
        in {
            None,
            "not_started",
            "not_required",
            "approved",
        }
    ):
        try:
            validate_source_job(raw, engine.agent_registry)
            can_schedule = True
        except RelayError as exc:
            schedule_reason = exc.code
    else:
        schedule_reason = "SCHEDULE_NOT_ELIGIBLE"
    detail["receipt_schema_version"] = raw.get("receipt_schema_version", RECEIPT_SCHEMA_VERSION)
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
    detail["review_status"] = raw.get("review_status") or "not_required"
    detail["workflow_status"] = (
        "needs_review"
        if detail["review_status"] in {"pending_human", "needs_human", "delivery_failed"}
        else "completed"
        if status in {"COMPLETED", "PARTIAL"}
        else str(status or "unknown").lower()
    )
    if raw.get("review_id"):
        from .reviews.service import ReviewService

        detail["review"] = ReviewService(engine.db, engine, engine.config).get(raw["review_id"])
        detail["actions"].update(
            {
                "can_review_confirm": detail["review_status"] in {"pending_human", "needs_human", "delivery_failed"},
                "can_review_rerun": detail["review_status"] in {"pending_human", "needs_human"},
                "can_review_reject": detail["review_status"] in {"pending_human", "needs_human"},
            }
        )
    detail["task_run_id"] = detail["job_id"]
    return detail


def job_result(db: Database, job_id: str, *, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
    path = Path(job["output_path"])
    if not path.is_file():
        return {"ok": True, "job_id": job_id, "task_run_id": job_id, "available": False, "path": str(path)}
    raw = path.read_bytes()
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    payload: dict[str, Any] = {
        "ok": True,
        "job_id": job_id,
        "task_run_id": job_id,
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
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
    artifacts = [item for item in db.artifacts_for_job(job_id) if item.get("publication_status") in (None, "published")]
    return {"ok": True, "job_id": job_id, "task_run_id": job_id, "artifacts": artifacts}


def job_events(db: Database, job_id: str) -> dict[str, Any]:
    if not db.get_job(job_id):
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
    return {"ok": True, "job_id": job_id, "task_run_id": job_id, "events": db.events_for_job(job_id)}


def check_job_progress(engine, job_id: str) -> dict[str, Any]:
    job = engine.db.get_job(job_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
    result = diagnose_progress(
        job,
        engine.progress_for_job(job_id),
        engine.db.attempts_for_job(job_id),
    )
    result["task_run_id"] = job_id
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
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {job_id}")
    attempts = {int(row["attempt_id"]): row for row in db.attempts_for_job(job_id)}
    attempt = attempts.get(attempt_id)
    if not attempt:
        raise RelayError("INVALID_REQUEST", "The Attempt does not belong to this Task Run.")
    if stream not in {"stdout", "stderr"}:
        raise RelayError("INVALID_REQUEST", "The log stream must be stdout or stderr.")
    if limit < 1 or limit > 65536:
        raise RelayError("INVALID_REQUEST", "The log limit must be between 1 and 65536 bytes.")
    path_value = attempt.get(f"{stream}_path")
    if not path_value:
        return {
            "ok": True,
            "job_id": job_id,
            "task_run_id": job_id,
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
            "task_run_id": job_id,
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
        "task_run_id": job_id,
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
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {run_id}")
    return {
        "ok": True,
        "job_id": run_id,
        "task_run_id": run_id,
        "run_id": run_id,
        "inputs": db.lineage_for_job(run_id),
        "outputs": [
            item for item in db.artifacts_for_job(run_id) if item.get("publication_status") in (None, "published")
        ],
    }


def artifact_detail(db: Database, artifact_uid: str) -> dict[str, Any]:
    artifact = db.artifact_by_uid(artifact_uid)
    if not artifact or artifact.get("publication_status") not in (None, "published"):
        raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {artifact_uid}")
    return {"ok": True, "artifact": artifact}


def artifact_lineage(db: Database, artifact_uid: str) -> dict[str, Any]:
    artifact = db.artifact_by_uid(artifact_uid)
    if not artifact or artifact.get("publication_status") not in (None, "published"):
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
                "task_run_id": row["job_id"],
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
                "task_run_id": row.get("job_id"),
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
    artifact = db.artifact_by_uid(artifact_uid)
    if not artifact or artifact.get("publication_status") not in (None, "published"):
        raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {artifact_uid}")
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
    public = {**task, "fallback_enabled": bool(task.get("fallback_enabled", 1))}
    raw = task.get("review_policy_json")
    if raw:
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(decoded, dict):
                public["review_policy"] = decoded
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return public


def list_tasks(engine, *, name: str | None = None, limit: int = 200) -> dict[str, Any]:
    return {"ok": True, "tasks": [_task_public(t) for t in engine.db.list_tasks(name=name, limit=limit)]}


def catalog_capability() -> dict[str, Any]:
    return {
        "ok": True,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "response_contract": {
            "list_items_key": "items",
            "status_style": "lowercase",
            "cursor_style": "opaque_urlsafe",
        },
        "resources": {
            "artifact_content": {
                "path_template": "/v1/artifacts/{artifact_uid}/content",
                "text_field": "text",
                "availability_field": "available",
            }
        },
        "kinds": {
            "tasks": {
                "list_path": "/v1/catalog/tasks",
                "detail_path_template": "/v1/tasks/{task_id}",
                "order": "updated_at_desc",
            },
            "task_runs": {
                "list_path": "/v1/catalog/task-runs",
                "detail_path_template": "/v1/task-runs/{task_run_id}",
                "order": "created_at_desc",
            },
            "projects": {
                "item_schema_version": 1,
                "list_path": "/v1/catalog/projects",
                "detail_path_template": "/v1/projects/{project_id}",
                "order": "updated_at_desc",
            },
            "project_runs": {
                "item_schema_version": 1,
                "list_path": "/v1/catalog/project-runs",
                "detail_path_template": "/v1/project-runs/{project_run_id}",
                "order": "created_at_desc",
            },
        },
    }


def _catalog_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task["task_id"],
        "name": task["name"],
        "version": task.get("version"),
        "task_summary": task.get("task_summary"),
        "has_input_schema": bool(task.get("input_schema")),
        "has_output_contract": bool(task.get("output_contract")),
        "has_validation_policy": bool(task.get("validation_policy")),
        "default_worker": task.get("default_worker"),
        "profile": task.get("profile"),
        "result_format": task.get("result_format"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }


def catalog_tasks(
    db: Database,
    *,
    limit: int = 100,
    cursor: str | None = None,
    updated_since: str | None = None,
) -> dict[str, Any]:
    limit = normalize_limit(limit, default=100, maximum=200)
    rows = db.catalog_tasks(limit=limit, cursor=_decode_catalog_cursor(cursor), updated_since=updated_since)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        next_cursor = _encode_cursor((rows[-1]["updated_at"], rows[-1]["task_id"]))
    items = [_catalog_task(row) for row in rows]
    return {
        "ok": True,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "kind": "tasks",
        "items": items,
        "tasks": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def _task_version(job: dict[str, Any]) -> int | None:
    value = None
    snapshot = job.get("task_snapshot_json")
    if snapshot:
        try:
            decoded = json.loads(snapshot)
            if isinstance(decoded, dict):
                value = decoded.get("task_version")
                if value is None:
                    value = (decoded.get("task_definition") or {}).get("version")
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _catalog_task_run(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "").lower()
    failure_reason = None
    if status == "failed":
        failure_reason = normalize_summary(
            job.get("error_message"), max_chars=1000, field="failure_reason", error_code="TASK_INVALID"
        )
    roles = [item for item in str(job.get("artifact_roles") or "").split(",") if item]
    output_path = job.get("output_path")
    return {
        "task_run_id": job["job_id"],
        "task_id": job.get("task_id"),
        "task_version": _task_version(job),
        "status": status,
        "task_summary": job.get("task_summary"),
        "result_summary": job.get("result_summary"),
        "failure_reason": failure_reason,
        "worker": job.get("actual_worker") or job.get("requested_worker"),
        "requested_worker": job.get("requested_worker"),
        "trigger_type": job.get("trigger_type") or "manual",
        "result_format": job.get("format"),
        "result_available": bool(output_path and Path(output_path).is_file()),
        "artifact_count": int(job.get("artifact_count") or 0),
        "artifact_roles": roles,
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
    }


def catalog_task_runs(
    db: Database,
    *,
    limit: int = 100,
    cursor: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    limit = normalize_limit(limit, default=100, maximum=200)
    if status:
        status = RESULT_STATUS.get(status.lower(), status.upper())
    rows = db.catalog_task_runs(
        limit=limit,
        cursor=_decode_catalog_cursor(cursor),
        status=status,
        task_id=task_id,
        date_from=date_from,
        date_to=date_to,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        next_cursor = _encode_cursor((rows[-1]["created_at"], rows[-1]["job_id"]))
    items = [_catalog_task_run(row) for row in rows]
    return {
        "ok": True,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "kind": "task_runs",
        "items": items,
        "task_runs": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def create_task(engine, payload: dict[str, Any]) -> dict[str, Any]:
    from .models import TaskSpec

    spec = TaskSpec(
        name=str(payload.get("name") or "").strip(),
        instructions=payload.get("instructions") or payload.get("task") or "",
        description=payload.get("description"),
        task_summary=payload.get("task_summary"),
        default_worker=payload.get("default_worker") or payload.get("worker"),
        default_model=payload.get("default_model") or payload.get("model"),
        fallback_enabled=bool(payload.get("fallback_enabled", True)),
        timeout_seconds=payload.get("timeout_seconds"),
        profile=payload.get("profile"),
        result_format=payload.get("result_format") or payload.get("format"),
        input_schema=payload.get("input_schema"),
        output_contract=payload.get("output_contract"),
        validation_policy=payload.get("validation_policy"),
        review_policy=payload.get("review_policy"),
    )
    task = engine.create_task(spec)
    return {"ok": True, "task": _task_public(task)}


def list_profiles(engine) -> dict[str, Any]:
    return {"ok": True, "profiles": engine.profiles.list()}


def create_profile(engine, payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "profile": engine.profiles.create(payload)}


def update_profile(engine, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "profile": engine.profiles.update(profile_id, payload)}


def delete_profile(engine, profile_id: str) -> dict[str, Any]:
    engine.profiles.delete(profile_id)
    return {"ok": True, "profile_id": profile_id, "deleted": True}


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
    if not isinstance(overrides, dict):
        raise RelayError("INVALID_REQUEST", "request must be an object.")
    request = None
    request_fields = {
        "worker",
        "format",
        "profile",
        "timeout_seconds",
        "fallback",
        "attachments",
        "artifact_inputs",
        "request_id",
        "inputs",
        "model",
        "review_mode",
    }
    if overrides or any(field in payload for field in request_fields):

        def value(name, default=None):
            return overrides[name] if name in overrides else payload.get(name, default)

        request = JobRequest(
            task=value("task", "") or "",
            worker=value("worker", "auto") or "auto",
            result_format=value("result_format", value("format", "json")) or "json",
            profile=value("profile"),
            timeout_seconds=value("timeout_seconds"),
            fallback=value("fallback"),
            attachments=list(value("attachments", []) or []),
            artifact_inputs=list(value("artifact_inputs", []) or []),
            request_id=value("request_id"),
            caller=overrides.get("caller", "human"),
            inputs=dict(value("inputs", {}) or {}),
            model=value("model"),
            review_mode=value("review_mode", "inherit") or "inherit",
        )
    job, reused, task = engine.run_task(
        task_id,
        request=request,
        queued=bool(payload.get("queued", False)),
        submitted_via=payload.get("submitted_via"),
    )
    if job.get("job_id"):
        job["task_run_id"] = job["job_id"]
    return {"ok": True, "run": job, "reused": reused, "task": _task_public(task)}


def runs_for_task(engine, task_id: str, *, limit: int = 50) -> dict[str, Any]:
    if not engine.db.get_task(task_id):
        raise RelayError("TASK_NOT_FOUND", f"Task not found: {task_id}")
    runs = engine.db.runs_for_task(task_id, limit=limit)
    for run in runs:
        if run.get("job_id"):
            run["task_run_id"] = run["job_id"]
    return {"ok": True, "task_id": task_id, "runs": runs}


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


def list_projects(engine, *, name: str | None = None, limit: int = 200) -> dict[str, Any]:
    projects = [_project_public(p) for p in engine.project_service.list_projects(name=name, limit=limit)]
    return {
        "ok": True,
        "kind": "projects",
        "items": projects,
        "projects": projects,
    }


def _project_definition(project: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(project.get("definition_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _catalog_project(project: dict[str, Any]) -> dict[str, Any]:
    definition = _project_definition(project)
    nodes = definition.get("nodes") or []
    connections = definition.get("connections") or []
    outputs = definition.get("output_selection") or []
    return {
        "project_id": project["project_id"],
        "name": project.get("name"),
        "version": project.get("version"),
        "project_summary": project.get("project_summary") or project.get("description") or project.get("name"),
        "node_count": len(nodes),
        "connection_count": len(connections),
        "output_roles": sorted({str(item.get("role")) for item in outputs if item.get("role")}),
        "has_checkpoints": any(bool(item.get("checkpoint")) for item in nodes if isinstance(item, dict)),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
    }


def catalog_projects(
    db: Database,
    *,
    limit: int = 100,
    cursor: str | None = None,
    updated_since: str | None = None,
) -> dict[str, Any]:
    limit = normalize_limit(limit, default=100, maximum=200)
    rows = db.catalog_projects(limit=limit, cursor=_decode_catalog_cursor(cursor), updated_since=updated_since)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor((rows[-1]["updated_at"], rows[-1]["project_id"])) if has_more and rows else None
    items = [_catalog_project(row) for row in rows]
    return {
        "ok": True,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "kind": "projects",
        "items": items,
        "projects": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def _project_run_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(run.get("project_snapshot_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _catalog_project_run(run: dict[str, Any]) -> dict[str, Any]:
    snapshot = _project_run_snapshot(run)
    warnings: list[dict[str, Any]] = []
    try:
        warning_value = json.loads(run.get("warnings_json") or "[]")
        if isinstance(warning_value, list):
            warnings = [item for item in warning_value if isinstance(item, dict)]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    final_ids: list[dict[str, Any]] = []
    try:
        value = json.loads(run.get("final_artifact_ids_json") or "[]")
        if isinstance(value, list):
            final_ids = [item for item in value if isinstance(item, dict)]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    status = str(run.get("status") or "").lower()
    workflow_status = "needs_review" if int(run.get("pending_review_count") or 0) else status
    failure_reason = None
    if status == "failed":
        failure_reason = normalize_summary(
            run.get("error_message") or run.get("failure_reason"),
            max_chars=1000,
            field="failure_reason",
            error_code="PROJECT_INVALID",
        )
        if not failure_reason:
            if warnings and isinstance(warnings[0], dict):
                failure_reason = normalize_summary(
                    warnings[0].get("error_message") or warnings[0].get("error"),
                    max_chars=1000,
                    field="failure_reason",
                    error_code="PROJECT_INVALID",
                )
    return {
        "project_run_id": run["project_run_id"],
        "project_id": run.get("project_id"),
        "project_name": run.get("project_name"),
        "project_version": run.get("project_version"),
        "project_summary": snapshot.get("project_summary"),
        "status": status,
        "workflow_status": workflow_status,
        "step_count": int(run.get("step_count") or 0),
        "completed_step_count": int(run.get("completed_step_count") or 0),
        "failed_step_count": int(run.get("failed_step_count") or 0),
        "blocked_step_count": int(run.get("blocked_step_count") or 0),
        "failed_node_id": run.get("failed_node_id"),
        "final_artifact_count": len(final_ids),
        "final_artifact_roles": sorted({str(item.get("role")) for item in final_ids if item.get("role")}),
        "failure_reason": failure_reason,
        "trigger_type": run.get("trigger_type") or "manual",
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
    }


def catalog_project_runs(
    db: Database,
    *,
    limit: int = 100,
    cursor: str | None = None,
    status: str | None = None,
    project_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    limit = normalize_limit(limit, default=100, maximum=200)
    if status:
        status = str(status).lower()
    rows = db.catalog_project_runs(
        limit=limit,
        cursor=_decode_catalog_cursor(cursor),
        status=status,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor((rows[-1]["created_at"], rows[-1]["project_run_id"])) if has_more and rows else None
    items = [_catalog_project_run(row) for row in rows]
    return {
        "ok": True,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "kind": "project_runs",
        "items": items,
        "project_runs": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


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


def project_runs(engine, project_id: str, *, limit: int = 50) -> dict[str, Any]:
    rows = engine.db.list_project_runs(project_id=project_id, limit=limit)
    items = [
        _project_run_public(r, json.loads(r["project_snapshot_json"]) if r.get("project_snapshot_json") else None)
        for r in rows
    ]
    return {
        "ok": True,
        "project_id": project_id,
        "kind": "project_runs",
        "items": items,
        "project_runs": items,
    }


def project_run(engine, project_run_id: str) -> dict[str, Any]:
    run = engine.db.get_project_run(project_run_id)
    if not run:
        raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
    from .reviews.service import ReviewService

    reviews = [
        item
        for item in ReviewService(engine.db, engine, engine.config).list(limit=200)["reviews"]
        if item.get("project_run_id") == project_run_id
    ]
    actionable = any(item.get("status") in {"pending_human", "needs_human", "delivery_failed"} for item in reviews)
    public = _project_run_public(
        run, json.loads(run["project_snapshot_json"]) if run.get("project_snapshot_json") else None
    )
    public["workflow_status"] = "needs_review" if actionable else str(run.get("status") or "unknown")
    return {
        "ok": True,
        "project_run": public,
        "reviews": reviews,
    }


def project_run_steps(engine, project_run_id: str) -> dict[str, Any]:
    steps = engine.db.list_project_steps(project_run_id)
    return {"ok": True, "project_run_id": project_run_id, "steps": [_step_public(s) for s in steps]}


def project_run_receipt(engine, project_run_id: str) -> dict[str, Any]:
    receipt = engine.project_service.project_run_receipt(project_run_id)
    return {"ok": True, "receipt": receipt}


def project_run_orchestrator(engine, project_run_id: str) -> dict[str, Any]:
    from .orchestrator.supervisor import Supervisor

    run = engine.db.get_project_run(project_run_id)
    if not run:
        raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
    snapshot = json.loads(run["project_snapshot_json"])
    config = Supervisor.config_from_snapshot(snapshot)
    events = engine.db.list_project_run_events(project_run_id)
    state = engine.db.get_orchestrator_state(project_run_id) or {}
    budget = None
    if config:
        supervisor = Supervisor(engine.db, engine)
        budget = {
            "llm_calls_used": int(state.get("llm_calls_used") or 0),
            "max_llm_calls_per_run": config.get("max_llm_calls_per_run"),
            "repair_attempts_used": supervisor.repair_attempts_used(project_run_id, None),
            "max_repair_attempts_per_node": config.get("max_repair_attempts_per_node"),
            "max_repair_attempts_per_run": config.get("max_repair_attempts_per_run"),
        }
    return {
        "ok": True,
        "project_run_id": project_run_id,
        "enabled": bool(config),
        "events": [
            {
                "event_id": e["event_id"],
                "node_id": e.get("node_id"),
                "seq": e["seq"],
                "kind": e["kind"],
                "actor": e["actor"],
                "summary": e["summary"],
                "detail": json.loads(e["detail_json"]) if e.get("detail_json") else None,
                "created_at": e["created_at"],
            }
            for e in events
        ],
        "budget": budget,
        # Promotion proposals (suggesting a recurring repair be made permanent in the
        # registered Project/Task definition) are not generated by any Task yet; the
        # field is reserved so this response shape does not need to change again once
        # that lands.
        "promotion_proposals": [],
    }


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
    result = engine.routine_service.preview(payload, limit=int(payload.get("limit", 5)))
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
    review = engine.db.review_session_for_approval(token) if hasattr(engine.db, "review_session_for_approval") else None
    if review and review.get("project_run_id") == project_run_id:
        from .reviews.service import ReviewService

        return ReviewService(engine.db, engine, engine.config).confirm(review["review_id"])
    from .approvals.service import ApprovalService

    service = ApprovalService(engine.db, engine, engine.config)
    reviewer = str(payload.get("reviewer") or "human")
    res = service.approve(project_run_id, token, reviewer=reviewer)
    return {"ok": True, **res}


def reject_checkpoint(engine, project_run_id: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    review = engine.db.review_session_for_approval(token) if hasattr(engine.db, "review_session_for_approval") else None
    if review and review.get("project_run_id") == project_run_id:
        from .reviews.service import ReviewService

        return ReviewService(engine.db, engine, engine.config).reject(
            review["review_id"], str(payload.get("reason") or "Rejected")
        )
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


# --- Result review gates ---


def list_reviews(engine, *, status: str | None = None, limit: int = 100) -> dict[str, Any]:
    from .reviews.service import ReviewService

    return ReviewService(engine.db, engine, engine.config).list(status=status, limit=limit)


def get_review(engine, review_id: str) -> dict[str, Any]:
    from .reviews.service import ReviewService

    return ReviewService(engine.db, engine, engine.config).get(review_id)


def task_run_review(engine, task_run_id: str) -> dict[str, Any]:
    job = engine.db.get_job(task_run_id)
    if not job:
        raise RelayError("JOB_NOT_FOUND", f"Task Run not found: {task_run_id}")
    review_id = job.get("review_id")
    if not review_id:
        return {"ok": True, "task_run_id": task_run_id, "review": None}
    return get_review(engine, review_id)


def project_run_reviews(engine, project_run_id: str) -> dict[str, Any]:
    from .reviews.service import ReviewService

    if not engine.db.get_project_run(project_run_id):
        raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
    reviews = ReviewService(engine.db, engine, engine.config).list(limit=200)["reviews"]
    return {
        "ok": True,
        "project_run_id": project_run_id,
        "reviews": [item for item in reviews if item.get("project_run_id") == project_run_id],
    }


def confirm_review(engine, review_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from .reviews.service import ReviewService

    return ReviewService(engine.db, engine, engine.config).confirm(review_id)


def rerun_review(engine, review_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .reviews.service import ReviewService

    return ReviewService(engine.db, engine, engine.config).rerun(review_id, str(payload.get("comment") or ""))


def reject_review(engine, review_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .reviews.service import ReviewService

    return ReviewService(engine.db, engine, engine.config).reject(review_id, str(payload.get("reason") or ""))


def retry_review_delivery(engine, review_id: str) -> dict[str, Any]:
    from .reviews.service import ReviewService

    return ReviewService(engine.db, engine, engine.config).retry_delivery(review_id)


# --- Phase 6b Comparison ---


def compare_runs(engine, a_run_id: str, b_run_id: str) -> dict[str, Any]:
    from .comparison.service import ComparisonService

    service = ComparisonService(engine.db, engine.config)
    res = service.compare_runs(a_run_id, b_run_id)
    return {"ok": True, "comparison": res}


def diff_artifacts(engine, a_uid: str, b_uid: str, max_bytes: int = 262144) -> dict[str, Any]:
    from .comparison.service import ComparisonService

    service = ComparisonService(engine.db, engine.config)
    res = service.diff_artifacts(a_uid, b_uid, max_bytes=max_bytes)
    return {"ok": True, "diff": res}


def partial_reexecute_project_run(engine, project_run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from_node = str(payload.get("from_node") or "")
    if not from_node:
        raise RelayError("INVALID_REQUEST", "from_node is required for partial_reexecute")
    cascade = bool(payload.get("cascade", True))
    worker = payload.get("worker")
    instruction_addendum = payload.get("instruction_addendum")
    if instruction_addendum is not None:
        if not isinstance(instruction_addendum, str):
            raise RelayError("INVALID_REQUEST", "instruction_addendum must be a string")
        if len(instruction_addendum) > 4000:
            raise RelayError("INVALID_REQUEST", "instruction_addendum must be 4000 characters or fewer")
    res = engine.project_service.partial_reexecute(
        project_run_id, from_node=from_node, cascade=cascade, worker=worker, instruction_addendum=instruction_addendum
    )
    return {"ok": True, **res}


# --- Phase 6c Semantic Search & Quality Scoring ---


def semantic_search_api(engine, payload: dict[str, Any]) -> dict[str, Any]:
    from .search.embedding import get_embedding_backend
    from .search.semantic import semantic_search

    query = str(payload.get("query") or "")
    kind = str(payload.get("kind") or "runs")
    limit = int(payload.get("limit") or 20)
    backend = get_embedding_backend(engine.config)
    return semantic_search(engine.db, backend, query, kind=kind, limit=limit)


def run_quality_api(engine, run_id: str) -> dict[str, Any]:
    from .quality.service import QualityService

    qs = QualityService(engine.db)
    quality = qs.score_run(run_id)
    return {"ok": True, "quality": quality}


def quality_attention_api(engine, status_filter: str = "low", limit: int = 50) -> dict[str, Any]:
    from .quality.service import QualityService

    qs = QualityService(engine.db)
    items = qs.attention_runs(status_filter=status_filter, limit=limit)
    return {"ok": True, "status": status_filter, "items": items}


# --- Phase 6d Observability ---


def attention_inbox_api(engine, kind: str | None = None, limit: int = 50) -> dict[str, Any]:
    from .attention.service import AttentionService

    svc = AttentionService(engine.db)
    items = svc.list_items(kind=kind, limit=limit)
    return {"ok": True, "items": items}


def operations_routines_api(engine, limit: int = 50) -> dict[str, Any]:
    from .operations.service import OperationsDashboardService

    svc = OperationsDashboardService(engine.db)
    return {"ok": True, "routines": svc.routine_dashboard(limit=limit)}


def operations_projects_api(engine, limit: int = 50) -> dict[str, Any]:
    from .operations.service import OperationsDashboardService

    svc = OperationsDashboardService(engine.db)
    return {"ok": True, "projects": svc.project_dashboard(limit=limit)}


def notify_test_api(engine, payload: dict[str, Any]) -> dict[str, Any]:
    from .notifications.sink import WebhookSink

    url = str(payload.get("url") or "")
    secret = payload.get("secret")
    data = payload.get("payload") or {"test": True}
    sink = WebhookSink(engine.config)
    try:
        res = sink.deliver(url, secret, data)
    except RelayError as exc:
        res = {"ok": False, "status_code": None, "error": exc.message}
    return {"ok": True, "delivery": res}


def get_receipt_schema_version() -> dict[str, Any]:
    return {"ok": True, "receipt_schema_version": RECEIPT_SCHEMA_VERSION}


# --- Phase 6e Data Lifecycle ---


def export_data_api(engine, payload: dict[str, Any]) -> dict[str, Any]:
    from .lifecycle.export_service import ExportService

    service = ExportService(engine.db, engine.config)
    include_runs = bool(payload.get("include_runs", False))
    out_path = payload.get("out_path")
    dest = service.export(include_runs=include_runs, out_path=out_path)
    return {"ok": True, "archive_path": str(dest)}


def import_data_api(engine, payload: dict[str, Any]) -> dict[str, Any]:
    from .lifecycle.import_service import ImportService

    service = ImportService(engine.db, engine.config)
    archive_path = str(payload.get("archive_path") or "")
    if not archive_path:
        raise RelayError("INVALID_REQUEST", "archive_path is required for import")
    conflict = str(payload.get("conflict") or "skip")
    include_runs = bool(payload.get("include_runs", False))
    res = service.import_archive(archive_path, conflict=conflict, include_runs=include_runs)
    return res
