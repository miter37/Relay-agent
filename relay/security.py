from __future__ import annotations

import platform
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import Config
from .errors import RelayError
from .util import is_within, safe_resolve


def _allowed(path: Path, roots: Iterable[str]) -> bool:
    return any(is_within(path, Path(root)) for root in roots)


def validate_requested_paths(
    config: Config,
    caller: str,
    output_path: Path,
    artifact_path: Path,
    *,
    extra_output_roots: Iterable[str] = (),
) -> None:
    service_mode = caller.lower() in {"hermes", "service", "daemon", "schedule"}
    if service_mode or not config.get("allow_manual_outside_roots", True):
        output_roots = [
            *config.get("allowed_output_roots", []),
            str(config.home / "schedule-outputs"),
            *extra_output_roots,
        ]
        artifact_roots = [
            *config.get("allowed_artifact_roots", []),
            str(config.home / "schedule-outputs"),
            *extra_output_roots,
        ]
        if not _allowed(output_path, output_roots):
            raise RelayError("OUTPUT_PATH_NOT_ALLOWED", f"Output path is outside allowed roots: {output_path}")
        if not _allowed(artifact_path, artifact_roots):
            raise RelayError("ARTIFACT_PATH_NOT_ALLOWED", f"Artifact path is outside allowed roots: {artifact_path}")


def validate_attachment_paths(config: Config, caller: str, attachments: list[str]) -> None:
    service_mode = caller.lower() in {"hermes", "service", "daemon", "schedule"}
    if not service_mode:
        return
    roots = [*config.get("allowed_input_roots", []), str(config.home / "schedule-inputs")]
    for value in attachments:
        path = safe_resolve(Path(value))
        if not _allowed(path, roots):
            raise RelayError("OUTPUT_PATH_NOT_ALLOWED", f"Attachment is outside allowed input roots: {path}")


def security_posture(config: Config) -> dict:
    return {
        "platform": platform.system(),
        "service_isolation_required": True,
        "service_isolation_acknowledged": bool(config.get("service_isolation_acknowledged", False)),
        "antigravity_security_verified": bool(config.get("workers.antigravity.security_verified", False)),
        "allowed_input_roots": config.get("allowed_input_roots", []),
        "allowed_output_roots": config.get("allowed_output_roots", []),
        "allowed_artifact_roots": config.get("allowed_artifact_roots", []),
        "warning": (
            "Unattended provider modes can execute powerful tools. Run Hermes workloads under a dedicated "
            "low-privilege OS account with filesystem access limited to Relay roots. Use NTFS ACLs on Windows "
            "and owner-only Unix permissions or a dedicated service account on Linux/macOS."
        ),
    }


def enabled_worker_health(engine) -> dict[str, Any]:
    """Report the current verification state of every enabled Agent."""
    healthy: list[str] = []
    unhealthy: list[dict[str, str]] = []
    for definition in engine.agent_registry.list_enabled_agents():
        agent_id = str(definition.get("agent_id") or "")
        if not agent_id:
            continue
        try:
            engine.agent_registry.get_adapter(agent_id).require_verified()
        except RelayError as exc:
            unhealthy.append({"agent_id": agent_id, "code": exc.code, "message": exc.message})
        except Exception as exc:  # Health reporting must include a broken adapter instead of failing whole health.
            unhealthy.append({"agent_id": agent_id, "code": "HEALTH_CHECK_FAILED", "message": str(exc)})
        else:
            healthy.append(agent_id)
    return {
        "status": "healthy" if healthy and not unhealthy else "unhealthy" if unhealthy else "no-active-engines",
        "enabled": [*healthy, *(item["agent_id"] for item in unhealthy)],
        "healthy": healthy,
        "unhealthy": unhealthy,
    }


def antigravity_setup(config: Config, engine) -> dict[str, Any]:
    adapter = engine.agent_registry.get_adapter("antigravity")
    spec = adapter.load_spec()
    version = adapter.version()
    current_audit = bool(spec and spec.version == version and spec.status == "healthy" and spec.deep_ok)
    enabled = bool(config.get("workers.antigravity.enabled", False))
    verified = bool(config.get("workers.antigravity.security_verified", False))
    if enabled and verified and current_audit:
        state = "enabled"
    elif not adapter.executable():
        state = "unavailable"
    elif current_audit:
        state = "ready"
    else:
        state = "needs_audit"
    return {
        "agent_id": "antigravity",
        "state": state,
        "enabled": enabled,
        "security_verified": verified,
        "installed": bool(adapter.executable()),
        "version": version,
        "audit": (
            {
                "version": spec.version,
                "status": spec.status,
                "deep_ok": spec.deep_ok,
                "audited_at": spec.audited_at,
            }
            if spec
            else None
        ),
    }


def activate_antigravity(config: Config, engine, *, isolation_acknowledged: bool) -> dict[str, Any]:
    if isolation_acknowledged is not True:
        raise RelayError(
            "PERMISSION_BLOCKED",
            "Antigravity activation requires explicit confirmation of OS-level isolation.",
        )

    from .doctor import Doctor

    adapter = engine.agent_registry.get_adapter("antigravity")
    audit = None
    try:
        adapter.require_verified()
    except RelayError:
        report = Doctor(config, engine.db).audit(["antigravity"], deep=True)
        audit = (report.get("workers") or [{}])[0]
        if not report.get("ok"):
            raise RelayError(
                "AGENT_HEALTH_FAILED",
                "Antigravity deep doctor did not pass. It was not enabled.",
                details={"audit": audit},
            ) from None
        adapter = engine.agent_registry.get_adapter("antigravity")
        adapter.require_verified()

    worker = config.data["workers"]["antigravity"]
    worker["security_verified"] = True
    worker["enabled"] = True
    config.save()
    return {"ok": True, "antigravity": antigravity_setup(config, engine), "audit": audit}
