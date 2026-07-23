from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


def relay_home_id(home: Path) -> str:
    value = str(home.resolve())
    if os.name == "nt":
        value = value.casefold()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = value.strip().lstrip("v").split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"Invalid Relay version: {value}")
    return tuple(int(part) for part in parts)


@dataclass(frozen=True, slots=True)
class CompatibilityDecision:
    mode: str
    reason: str | None = None


def evaluate_compatibility(
    health: dict,
    *,
    gui_version: str,
    expected_relay_home_id: str | None = None,
    required_api_version: str = "v1",
    supported_schema_revision: int = 1,
) -> CompatibilityDecision:
    if not health.get("ok"):
        return CompatibilityDecision("read-only", "daemon health check failed")
    if required_api_version not in health.get("api_versions", []):
        return CompatibilityDecision("read-only", "daemon does not support the required API")
    if "api_schema_revision" in health and health.get("api_schema_revision") != supported_schema_revision:
        return CompatibilityDecision("read-only", "daemon API schema revision is not supported")
    try:
        if _version_tuple(gui_version) < _version_tuple(str(health.get("min_gui_version", "9999"))):
            return CompatibilityDecision("read-only", "GUI version is below the daemon minimum")
    except ValueError:
        return CompatibilityDecision("read-only", "invalid version in health response")
    if expected_relay_home_id and health.get("relay_home_id") != expected_relay_home_id:
        return CompatibilityDecision("disconnected", "Relay Home does not match")
    return CompatibilityDecision("normal")
