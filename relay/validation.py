from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import RelayError
from .util import is_within, sha256_file


REQUIRED_JSON_FIELDS = {
    "schema_version": str,
    "status": str,
    "answer": str,
    "sources": list,
    "uncertainties": list,
    "missing_items": list,
    "artifacts": list,
}


def validate_json_result(path: Path, max_bytes: int) -> dict[str, Any]:
    if not path.is_file():
        raise RelayError("OUTPUT_NOT_CREATED", f"Result file not found: {path}", True)
    size = path.stat().st_size
    if size == 0:
        raise RelayError("EMPTY_OUTPUT", "Result JSON is empty", True)
    if size > max_bytes:
        raise RelayError("SCHEMA_MISMATCH", f"Result JSON exceeds maximum size: {size}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise RelayError("INVALID_TEXT_ENCODING", "Result JSON is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise RelayError("INVALID_JSON", f"Result JSON parsing failed: {exc}", True) from exc
    if not isinstance(value, dict):
        raise RelayError("SCHEMA_MISMATCH", "Result JSON must be an object", True)
    for field, expected in REQUIRED_JSON_FIELDS.items():
        if field not in value or not isinstance(value[field], expected):
            raise RelayError("SCHEMA_MISMATCH", f"Field {field!r} is missing or has the wrong type", True)
    if value.get("schema_version") != "1.0":
        raise RelayError("SCHEMA_MISMATCH", "schema_version must be 1.0", True)
    if value["status"] not in {"complete", "partial", "failed"}:
        raise RelayError("SCHEMA_MISMATCH", "status must be complete, partial, or failed", True)
    for field in ("uncertainties", "missing_items"):
        if any(not isinstance(item, str) for item in value[field]):
            raise RelayError("SCHEMA_MISMATCH", f"{field} must contain only strings", True)
    if any(not isinstance(item, (str, dict)) for item in value["sources"]):
        raise RelayError("SCHEMA_MISMATCH", "sources must contain strings or objects", True)
    for item in value["artifacts"]:
        if not isinstance(item, (str, dict)):
            raise RelayError("SCHEMA_MISMATCH", "artifacts must contain strings or objects", True)
        if isinstance(item, dict) and not isinstance(item.get("relative_path"), str):
            raise RelayError("SCHEMA_MISMATCH", "artifact objects require relative_path", True)
        if isinstance(item, dict) and "content" in item:
            if not isinstance(item["content"], str):
                raise RelayError("SCHEMA_MISMATCH", "artifact content must be a string", True)
            if item.get("encoding") not in {"utf-8", "base64"}:
                raise RelayError("SCHEMA_MISMATCH", "artifact encoding must be utf-8 or base64", True)
            if not isinstance(item.get("description", ""), str):
                raise RelayError("SCHEMA_MISMATCH", "artifact description must be a string", True)
    return value


def materialize_artifact_payloads(
    value: dict[str, Any],
    artifact_dir: Path,
    max_files: int,
    max_total_bytes: int,
) -> list[str]:
    payloads: list[tuple[str, Path, bytes]] = []
    total = 0
    for item in value.get("artifacts", []):
        if not isinstance(item, dict) or "content" not in item:
            continue
        relative_path = item.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
            raise RelayError("ARTIFACT_PATH_VIOLATION", "Artifact payload path must be a non-empty POSIX path")
        pure_path = PurePosixPath(relative_path)
        if len(pure_path.parts) > 1 and pure_path.parts[0] == "artifacts":
            pure_path = PurePosixPath(*pure_path.parts[1:])
            relative_path = pure_path.as_posix()
            item["relative_path"] = relative_path
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise RelayError("ARTIFACT_PATH_VIOLATION", f"Artifact payload escaped its root: {relative_path}")
        target = artifact_dir.joinpath(*pure_path.parts)
        if not is_within(target, artifact_dir):
            raise RelayError("ARTIFACT_PATH_VIOLATION", f"Artifact payload escaped its root: {relative_path}")
        encoding = item.get("encoding")
        content = item.get("content")
        if not isinstance(content, str) or encoding not in {"utf-8", "base64"}:
            raise RelayError("SCHEMA_MISMATCH", f"Invalid artifact payload: {relative_path}", True)
        try:
            data = content.encode("utf-8") if encoding == "utf-8" else base64.b64decode(content, validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise RelayError("SCHEMA_MISMATCH", f"Artifact payload decoding failed: {relative_path}", True) from exc
        total += len(data)
        payloads.append((pure_path.as_posix(), target, data))
    if len(payloads) > max_files or total > max_total_bytes:
        raise RelayError("ARTIFACT_PATH_VIOLATION", "Artifact payload count or total size exceeds configured limits")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    materialized: list[str] = []
    for relative_path, target, data in payloads:
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file():
                raise RelayError("ARTIFACT_PATH_VIOLATION", f"Invalid existing artifact target: {relative_path}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if not is_within(target, artifact_dir):
            raise RelayError("ARTIFACT_PATH_VIOLATION", f"Artifact payload escaped its root: {relative_path}")
        try:
            with target.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            continue
        materialized.append(relative_path)
    return materialized


def validate_text_result(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        raise RelayError("OUTPUT_NOT_CREATED", f"Result file not found: {path}", True)
    size = path.stat().st_size
    if size == 0:
        raise RelayError("EMPTY_OUTPUT", "Text result is empty", True)
    if size > max_bytes:
        raise RelayError("SCHEMA_MISMATCH", f"Text result exceeds maximum size: {size}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RelayError("INVALID_TEXT_ENCODING", "Text result is not UTF-8") from exc
    if not text.strip():
        raise RelayError("EMPTY_OUTPUT", "Text result contains only whitespace", True)
    return text


def scan_artifacts(artifact_dir: Path, max_files: int, max_total_bytes: int) -> list[dict[str, Any]]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    total = 0
    for root, dirs, names in os.walk(artifact_dir, followlinks=False):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
        for name in names:
            path = root_path / name
            if path.is_symlink():
                raise RelayError("ARTIFACT_PATH_VIOLATION", f"Symbolic links are not allowed: {path}")
            if not is_within(path, artifact_dir):
                raise RelayError("ARTIFACT_PATH_VIOLATION", f"Artifact escaped its root: {path}")
            size = path.stat().st_size
            total += size
            if len(files) + 1 > max_files or total > max_total_bytes:
                raise RelayError("ARTIFACT_PATH_VIOLATION", "Artifact count or total size exceeds configured limits")
            rel = path.relative_to(artifact_dir).as_posix()
            mime, _ = mimetypes.guess_type(path.name)
            files.append({
                "name": path.name,
                "relative_path": rel,
                "mime_type": mime or "application/octet-stream",
                "size": size,
                "sha256": sha256_file(path),
            })
    return sorted(files, key=lambda x: x["relative_path"])


def reconcile_json_artifacts(value: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    descriptions: dict[str, str] = {}
    for item in value.get("artifacts", []):
        if isinstance(item, dict) and isinstance(item.get("relative_path"), str):
            descriptions[item["relative_path"]] = str(item.get("description", ""))
    value["artifacts"] = [
        {
            "name": item["name"],
            "relative_path": item["relative_path"],
            "description": descriptions.get(item["relative_path"], ""),
        }
        for item in artifacts
    ]
    return value
