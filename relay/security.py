from __future__ import annotations

import platform
from collections.abc import Iterable
from pathlib import Path

from .config import Config
from .errors import RelayError
from .util import is_within, safe_resolve


def _allowed(path: Path, roots: Iterable[str]) -> bool:
    return any(is_within(path, Path(root)) for root in roots)


def validate_requested_paths(config: Config, caller: str, output_path: Path, artifact_path: Path) -> None:
    service_mode = caller.lower() in {"hermes", "service", "daemon"}
    if service_mode or not config.get("allow_manual_outside_roots", True):
        if not _allowed(output_path, config.get("allowed_output_roots", [])):
            raise RelayError("OUTPUT_PATH_NOT_ALLOWED", f"Output path is outside allowed roots: {output_path}")
        if not _allowed(artifact_path, config.get("allowed_artifact_roots", [])):
            raise RelayError("ARTIFACT_PATH_NOT_ALLOWED", f"Artifact path is outside allowed roots: {artifact_path}")


def validate_attachment_paths(config: Config, caller: str, attachments: list[str]) -> None:
    service_mode = caller.lower() in {"hermes", "service", "daemon"}
    if not service_mode:
        return
    roots = config.get("allowed_input_roots", [])
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
