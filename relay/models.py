from __future__ import annotations

import json
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
    # None is a real "caller did not specify" sentinel here, matching `fallback`
    # below: run_task/run_task_from_snapshot merge a dispatch-built JobRequest
    # onto the Task snapshot's own profile with `request.profile or base.profile`,
    # so a non-None default would silently override every Task's configured
    # profile whenever the dispatcher (e.g. Project execution) doesn't set one.
    profile: str | None = None
    profile_snapshot: dict[str, Any] = field(default_factory=dict)
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
    inputs: dict[str, Any] = field(default_factory=dict)
    artifact_inputs: list[dict[str, str]] = field(default_factory=list)
    resolved_artifact_inputs: list[dict[str, Any]] = field(default_factory=list)
    # ``inherit`` keeps the registered Task/Project policy.  Human/API callers
    # may override it for one execution; Orchestrator review is Project-only.
    review_mode: str = "inherit"
    review_id: str | None = None

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


@dataclass(slots=True)
class TaskSpec:
    name: str
    instructions: str
    description: str | None = None
    task_summary: str | None = None
    default_worker: str | None = "auto"
    default_model: str | None = None
    fallback_enabled: bool = True
    timeout_seconds: int | None = None
    profile: str | None = None
    result_format: str | None = None
    input_schema: str | None = None
    output_contract: str | None = None
    validation_policy: str | None = None
    review_policy: dict[str, Any] | None = None
    task_id: str | None = None
    version: int = 1

    def validate(self) -> None:
        from .errors import RelayError

        if not str(self.name or "").strip():
            raise RelayError("TASK_NAME_REQUIRED", "A task name is required.")
        if not str(self.instructions or "").strip():
            raise RelayError("TASK_INVALID", "Task instructions are required.")
        if self.task_summary is not None:
            from .validation import normalize_summary

            self.task_summary = normalize_summary(
                self.task_summary,
                max_chars=500,
                field="task_summary",
                error_code="TASK_INVALID",
            )
        if self.result_format and self.result_format not in {"json", "txt"}:
            raise RelayError("TASK_INVALID", "result_format must be json or txt.")
        if self.input_schema:
            from .task_inputs import parse_schema

            try:
                parse_schema(self.input_schema)
            except ValueError as exc:
                raise RelayError("TASK_INVALID", str(exc)) from exc
        if self.output_contract not in (None, ""):
            from .task_interface import normalize_interface

            try:
                raw_contract = self.output_contract
                if isinstance(raw_contract, str):
                    raw_contract = json.loads(raw_contract)
                if isinstance(raw_contract, dict) and (
                    "interface_version" in raw_contract
                    or "artifact_inputs" in raw_contract
                    or "outputs" in raw_contract
                ):
                    self.output_contract = json.dumps(normalize_interface(raw_contract), ensure_ascii=False)
            except json.JSONDecodeError:
                # Keep pre-Interface legacy values loadable; the shared reader will
                # surface them as an actionable diagnostic when they are used.
                pass
            except (TypeError, ValueError) as exc:
                raise RelayError("TASK_INVALID", f"Invalid Task Interface contract: {exc}") from exc
        if self.review_policy is not None:
            if not isinstance(self.review_policy, dict):
                raise RelayError("TASK_INVALID", "review_policy must be an object.")
            unknown = set(self.review_policy) - {"enabled", "reviewer", "guidelines", "max_reruns"}
            if unknown:
                raise RelayError("TASK_INVALID", f"Unknown review_policy keys: {', '.join(sorted(unknown))}")
            if "enabled" in self.review_policy and not isinstance(self.review_policy["enabled"], bool):
                raise RelayError("TASK_INVALID", "review_policy.enabled must be a boolean.")
            if self.review_policy.get("reviewer", "human") != "human":
                raise RelayError("TASK_INVALID", "Standalone Task review_policy only supports reviewer=human.")
            guidelines = self.review_policy.get("guidelines")
            if guidelines is not None and (not isinstance(guidelines, str) or len(guidelines) > 8000):
                raise RelayError("TASK_INVALID", "review_policy guidelines must be text up to 8000 characters.")
            max_reruns = self.review_policy.get("max_reruns", 0)
            if not isinstance(max_reruns, int) or isinstance(max_reruns, bool) or not 0 <= max_reruns <= 20:
                raise RelayError("TASK_INVALID", "review_policy max_reruns must be between 0 and 20.")

    def to_row(self) -> dict[str, Any]:
        from .util import new_job_id, utc_now

        self.validate()
        now = utc_now()
        return {
            "task_id": self.task_id or new_job_id(),
            "name": self.name,
            "description": self.description,
            "task_summary": self.task_summary,
            "instructions": self.instructions,
            "default_worker": self.default_worker,
            "default_model": self.default_model,
            "fallback_enabled": 1 if self.fallback_enabled else 0,
            "timeout_seconds": self.timeout_seconds,
            "profile": self.profile,
            "result_format": self.result_format,
            "input_schema": self.input_schema,
            "output_contract": self.output_contract,
            "validation_policy": self.validation_policy,
            "review_policy_json": json.dumps(self.review_policy, ensure_ascii=False) if self.review_policy else None,
            "version": self.version,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def normalize_changes(changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name",
            "description",
            "task_summary",
            "instructions",
            "default_worker",
            "default_model",
            "fallback_enabled",
            "timeout_seconds",
            "profile",
            "result_format",
            "input_schema",
            "output_contract",
            "validation_policy",
            "review_policy",
        }
        out: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key == "fallback_enabled":
                out[key] = 1 if value else 0
            elif key == "review_policy":
                from .errors import RelayError

                if value is not None and not isinstance(value, dict):
                    raise RelayError("TASK_INVALID", "review_policy must be an object.")
                out["review_policy_json"] = json.dumps(value, ensure_ascii=False) if value else None
            elif key == "task_summary":
                from .validation import normalize_summary

                out[key] = normalize_summary(
                    value,
                    max_chars=500,
                    field="task_summary",
                    error_code="TASK_INVALID",
                )
            else:
                out[key] = value
        return out

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        request: dict[str, Any],
        *,
        name: str,
        description: str | None = None,
    ) -> TaskSpec:
        instructions = snapshot.get("task") or request.get("task") or ""
        worker = snapshot.get("worker") or request.get("worker") or "auto"
        fallback = snapshot.get("fallback")
        return cls(
            name=name,
            instructions=instructions,
            description=description,
            task_summary=snapshot.get("task_summary") or description,
            default_worker=worker,
            default_model=snapshot.get("model") or request.get("model"),
            fallback_enabled=bool(fallback) if fallback is not None else True,
            timeout_seconds=snapshot.get("timeout_seconds") or request.get("timeout_seconds"),
            profile=snapshot.get("profile") or request.get("profile"),
            result_format=snapshot.get("result_format") or request.get("result_format"),
            review_policy=snapshot.get("review_policy") or request.get("review_policy"),
        )
