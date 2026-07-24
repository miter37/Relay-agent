from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .util import utc_now

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET = re.compile(r"(?i)\b(api[_-]?key|token|authorization|password|secret)\b(\s*[:=]\s*)(\S+)")
_ISSUES = (
    (
        "INTERACTIVE_PROMPT",
        "The Agent may be waiting for permission or interactive input.",
        ("do you want to proceed", "allow this action", "approve this action", "press enter", "waiting for approval"),
    ),
    (
        "PERMISSION_BLOCKED",
        "A permission or sandbox problem may be blocking the Agent.",
        ("access denied", "permission denied", "operation not permitted", "sandbox violation", "not allowed"),
    ),
    (
        "AUTH_REQUIRED",
        "The Agent may need login or authentication.",
        ("unauthorized", "authentication failed", "auth required", "login required", "not logged in", "invalid token"),
    ),
    (
        "RATE_LIMITED",
        "The provider may be rate-limited or out of quota.",
        ("rate limit", "too many requests", "quota exceeded", "insufficient quota"),
    ),
    (
        "NETWORK_ERROR",
        "A network or provider connection problem may be delaying the task.",
        ("connection refused", "connection reset", "name resolution", "dns", "network is unreachable"),
    ),
    (
        "PROCESS_ERROR",
        "The Agent log contains a possible process failure.",
        ("traceback (most recent call last)", "fatal error", "panic:", "segmentation fault"),
    ),
)


def _tail(path_value: str | None, limit: int = 32768) -> tuple[str, float]:
    if not path_value:
        return "", 0.0
    path = Path(path_value)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            return handle.read().decode("utf-8", errors="replace"), path.stat().st_mtime
    except OSError:
        return "", 0.0


def _safe_line(text: str, limit: int = 300) -> str | None:
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    if not lines:
        return None
    line = _ANSI.sub("", lines[-1])
    line = "".join(character for character in line if character.isprintable() or character == "\t")
    line = _SECRET.sub(r"\1\2<redacted>", line)
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _elapsed(job: dict[str, Any], snapshot: dict[str, Any] | None) -> float | None:
    if snapshot and snapshot.get("elapsed_seconds") is not None:
        return round(float(snapshot["elapsed_seconds"]), 1)
    started_at = job.get("started_at")
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at))
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return round(max(0.0, (datetime.now(UTC) - started).total_seconds()), 1)
    except ValueError:
        return None


def diagnose_progress(
    job: dict[str, Any],
    snapshot: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    status = str(job.get("status") or "UNKNOWN")
    stage = str((snapshot or {}).get("stage") or status).casefold()
    latest_attempt = attempts[-1] if attempts else {}
    stdout, stdout_mtime = _tail(latest_attempt.get("stdout_path"))
    stderr, stderr_mtime = _tail(latest_attempt.get("stderr_path"))
    combined = f"{stderr}\n{stdout}".casefold()
    issue = None
    if snapshot and snapshot.get("prompt_detected"):
        issue = {"code": "INTERACTIVE_PROMPT", "message": _ISSUES[0][1]}
    else:
        for code, message, markers in _ISSUES:
            if any(marker in combined for marker in markers):
                issue = {"code": code, "message": message}
                break

    recent_stream = "stderr" if stderr_mtime >= stdout_mtime and stderr else "stdout"
    recent_line = _safe_line(stderr if recent_stream == "stderr" else stdout)
    idle = round(float(snapshot["idle_seconds"]), 1) if snapshot and snapshot.get("idle_seconds") is not None else None
    process_alive = snapshot.get("process_alive") if snapshot else None
    soft_stall = float((snapshot or {}).get("soft_stall_seconds") or job.get("soft_stall_seconds") or 120)

    if status in {"FAILED", "CANCELLED"}:
        level = "error"
        headline = "Task failed" if status == "FAILED" else "Task was cancelled"
        summary = str(job.get("error_message") or f"The task is {status.casefold()}.")
    elif status in {"COMPLETED", "PARTIAL"}:
        level = "done"
        headline = "Task finished"
        summary = f"The task is now {status.casefold()}."
    elif status == "QUEUED":
        level = "waiting"
        headline = "Waiting in queue"
        summary = "The task is waiting for an available execution slot."
    elif status == "PREPARING":
        level = "waiting"
        headline = "Preparing the task"
        summary = "Relay is preparing the workspace and Agent command."
    elif status == "VALIDATING":
        level = "ok"
        headline = "Checking the result"
        summary = "The Agent process has finished and Relay is validating its result and files."
    elif status == "DELIVERING":
        level = "ok"
        headline = "Delivering files"
        summary = "Relay is publishing the validated result and artifact files."
    elif issue:
        level = "attention"
        headline = "Possible issue detected"
        summary = issue["message"]
    elif idle is not None and idle >= soft_stall:
        level = "attention"
        headline = "No recent activity"
        summary = f"No observable Agent activity has occurred for {idle:.0f} seconds."
    elif process_alive is True and snapshot and snapshot.get("activity_observed"):
        level = "ok"
        headline = "Agent is active"
        summary = "The Agent process is running and Relay has observed recent output or file activity."
    elif process_alive is True:
        level = "waiting"
        headline = "Agent is running"
        summary = "The process is alive, but it has not produced observable output or file changes yet."
    elif process_alive is False and status == "RUNNING":
        level = "waiting"
        headline = "Agent process ended"
        summary = "Relay is collecting the Agent output or preparing the next processing stage."
    else:
        level = "waiting"
        headline = "Live process state unavailable"
        summary = "Relay has the Job status, but no live process telemetry is currently available."

    activity = {
        "kind": (snapshot or {}).get("last_activity_kind"),
        "stdout_bytes": (snapshot or {}).get("stdout_bytes"),
        "stderr_bytes": (snapshot or {}).get("stderr_bytes"),
        "workspace_files": (snapshot or {}).get("workspace_files"),
        "recent_stream": recent_stream if recent_line else None,
        "recent_line": recent_line,
    }
    return {
        "ok": True,
        "job_id": job.get("job_id"),
        "checked_at": utc_now(),
        "status": status,
        "stage": stage,
        "level": level,
        "headline": headline,
        "summary": summary,
        "worker": job.get("actual_worker") or job.get("requested_worker"),
        "elapsed_seconds": _elapsed(job, snapshot),
        "process_alive": process_alive,
        "idle_seconds": idle,
        "recent_activity": activity,
        "detected_issue": issue,
    }
