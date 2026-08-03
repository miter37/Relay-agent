from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..errors import RelayError


_VALID_TARGET_TYPES = {"task", "project"}
_VALID_OVERLAP = {"skip", "queue", "cancel_previous", "allow_parallel"}
_VALID_MISSED = {"skip", "run_once_on_recovery", "replay_all"}
_VALID_VERSION = {"latest", "pinned"}


@dataclass(slots=True)
class RoutineSpec:
    name: str
    target_type: str
    target_id: str
    rule: dict[str, Any]
    timezone: str
    overlap_policy: str = "skip"
    missed_policy: str = "skip"
    missed_grace_seconds: int = 43200
    version_policy: str = "latest"
    pinned_version: int | None = None
    input_policy: dict[str, Any] | None = None
    notification_policy: dict[str, Any] | None = None
    starts_at_utc: str | None = None
    ends_at_utc: str | None = None
    enabled: bool = True
    description: str | None = None
    routine_id: str | None = None

    def validate(self, *, task_lookup, project_lookup) -> None:
        if not self.name.strip():
            raise RelayError("ROUTINE_INVALID", "Routine name must be non-empty.")
        if self.target_type not in _VALID_TARGET_TYPES:
            raise RelayError("ROUTINE_INVALID", f"Unknown target_type: {self.target_type}")
        if self.target_type == "task":
            if not task_lookup(self.target_id):
                raise RelayError("ROUTINE_TARGET_MISSING", f"Task not found: {self.target_id}")
        elif self.target_type == "project":
            if not project_lookup(self.target_id):
                raise RelayError("ROUTINE_TARGET_MISSING", f"Project not found: {self.target_id}")
        if self.overlap_policy not in _VALID_OVERLAP:
            raise RelayError("ROUTINE_INVALID", f"Unknown overlap_policy: {self.overlap_policy}")
        if self.missed_policy not in _VALID_MISSED:
            raise RelayError("ROUTINE_INVALID", f"Unknown missed_policy: {self.missed_policy}")
        if self.version_policy not in _VALID_VERSION:
            raise RelayError("ROUTINE_INVALID", f"Unknown version_policy: {self.version_policy}")
        if self.pinned_version is not None and self.version_policy != "pinned":
            raise RelayError("ROUTINE_INVALID", "pinned_version requires version_policy=pinned")
        if self.pinned_version is None and self.version_policy == "pinned":
            raise RelayError("ROUTINE_INVALID", "version_policy=pinned requires pinned_version")
        if self.starts_at_utc and self.ends_at_utc and self.starts_at_utc > self.ends_at_utc:
            raise RelayError("ROUTINE_INVALID", "starts_at_utc must not exceed ends_at_utc")
        # Delegate rule validation to schedules.rules (reused).
        from ..schedules.rules import validate_rule, _timezone
        _timezone(self.timezone)
        # validate_rule expects timezone inside the rule dict.
        rule_with_tz = dict(self.rule)
        rule_with_tz.setdefault("timezone", self.timezone)
        validate_rule(rule_with_tz)

    def to_row(self) -> dict[str, Any]:
        from ..util import canonical_json
        return {
            "name": self.name,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "rule_json": canonical_json(self.rule),
            "timezone": self.timezone,
            "overlap_policy": self.overlap_policy,
            "missed_policy": self.missed_policy,
            "missed_grace_seconds": self.missed_grace_seconds,
            "version_policy": self.version_policy,
            "pinned_version": self.pinned_version,
            "input_policy_json": canonical_json(self.input_policy) if self.input_policy else None,
            "notification_policy_json": canonical_json(self.notification_policy) if self.notification_policy else None,
            "starts_at_utc": self.starts_at_utc,
            "ends_at_utc": self.ends_at_utc,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RoutineSpec:
        import json
        rule = payload.get("rule") or payload.get("rule_json")
        if isinstance(rule, str):
            rule = json.loads(rule)
        elif rule is None:
            rule = {}
        input_policy = payload.get("input_policy") or payload.get("input_policy_json")
        if isinstance(input_policy, str):
            input_policy = json.loads(input_policy)
        notification_policy = payload.get("notification_policy") or payload.get("notification_policy_json")
        if isinstance(notification_policy, str):
            notification_policy = json.loads(notification_policy)
        return cls(
            name=str(payload.get("name") or ""),
            target_type=str(payload.get("target_type") or ""),
            target_id=str(payload.get("target_id") or ""),
            rule=rule,
            timezone=str(payload.get("timezone") or "UTC"),
            overlap_policy=str(payload.get("overlap_policy", "skip")),
            missed_policy=str(payload.get("missed_policy", "skip")),
            missed_grace_seconds=int(payload.get("missed_grace_seconds", 43200)),
            version_policy=str(payload.get("version_policy", "latest")),
            pinned_version=payload.get("pinned_version"),
            input_policy=input_policy,
            notification_policy=notification_policy,
            starts_at_utc=payload.get("starts_at_utc"),
            ends_at_utc=payload.get("ends_at_utc"),
            enabled=bool(payload.get("enabled", True)),
            description=payload.get("description"),
            routine_id=payload.get("routine_id"),
        )
