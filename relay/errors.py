from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RelayError(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


ERROR_CODES = {
    "INVALID_REQUEST",
    "TASK_REQUIRED",
    "OUTPUT_PATH_NOT_ALLOWED",
    "ARTIFACT_PATH_NOT_ALLOWED",
    "WORKSPACE_PATH_NOT_ALLOWED",
    "OUTPUT_EXISTS",
    "WORKER_NOT_INSTALLED",
    "WORKER_DISABLED",
    "WORKER_UNVERIFIED",
    "WORKER_UNHEALTHY",
    "ATTACHMENT_NOT_FOUND",
    "AUTH_REQUIRED",
    "RATE_LIMITED",
    "QUOTA_EXCEEDED",
    "PERMISSION_BLOCKED",
    "INTERACTIVE_PROMPT_DETECTED",
    "STALL_TIMEOUT",
    "TIMEOUT",
    "PROCESS_CRASHED",
    "EMPTY_OUTPUT",
    "OUTPUT_NOT_CREATED",
    "INVALID_TEXT_ENCODING",
    "INVALID_JSON",
    "SCHEMA_MISMATCH",
    "ARTIFACT_PATH_VIOLATION",
    "CANCELLED",
    "ALL_WORKERS_FAILED",
    "DUPLICATE_REQUEST",
    "JOB_NOT_FOUND",
    "DAEMON_UNAVAILABLE",
    "DAEMON_RESTARTED",
    "DELIVERY_FAILED",
    "CAPABILITY_AUDIT_FAILED",
    "MODEL_DISCOVERY_FAILED",
    "UNSUPPORTED_WORKER_VERSION",
    "INTERNAL_ERROR",
}
