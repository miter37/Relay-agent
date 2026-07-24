from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class JobRequest:
    task: str
    title: str | None = None
    task_file: str | None = None
    worker: str = "auto"
    fallback: bool | None = None
    fallback_agents: list[str] | None = None
    result_format: str = "json"
    output_path: str | None = None
    artifact_path: str | None = None
    profile: str = "web-research"
    timeout_seconds: int | None = None
    caller: str = "human"
    request_id: str | None = None
    attachments: list[str] = field(default_factory=list)
    workspace: str | None = None
    target_path: str | None = None
    overwrite: bool = False
    machine: bool = False
    force_new: bool = False
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> JobRequest:
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in value.items() if k in allowed})


@dataclass(slots=True)
class ProcessOutcome:
    exit_code: int | None
    timed_out: bool
    stalled: bool
    cancelled: bool
    interactive_prompt_detected: bool
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path
    command: list[str]
    failure_code: str | None = None


@dataclass(slots=True)
class AdapterSpec:
    worker: str
    executable: str | None
    version: str | None
    audited_at: str
    help_hash: str | None
    shallow_ok: bool
    deep_ok: bool
    unattended_ok: bool
    output_ok: bool
    artifact_ok: bool
    status: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AdapterSpec:
        return cls(**value)


@dataclass(slots=True)
class AttemptResult:
    worker: str
    status: str
    result_status: str | None
    staged_result: Path | None
    staged_artifacts: Path | None
    receipt: dict[str, Any]
    failure_code: str | None = None
    failure_message: str | None = None
    retryable: bool = False
