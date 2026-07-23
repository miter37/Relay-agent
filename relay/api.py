from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from .db import Database
from .errors import RelayError

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
