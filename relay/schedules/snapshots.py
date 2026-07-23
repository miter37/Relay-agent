from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent_registry import AgentRegistry
from ..config import Config
from ..errors import RelayError
from ..models import JobRequest
from ..util import ensure_dir, is_within, safe_resolve


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    schedule_id: str
    root: Path
    task_file: Path
    attachments: tuple[Path, ...]
    manifest_path: Path


def _snapshot_root(config: Config) -> Path:
    return safe_resolve(config.home / "schedule-inputs")


def _has_symlink_component(path: Path) -> bool:
    current = path
    while current != current.parent:
        if current.is_symlink():
            return True
        current = current.parent
    return current.is_symlink()


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def validate_source_job(job: dict[str, Any], registry: AgentRegistry) -> JobRequest:
    if job.get("status") != "COMPLETED" or job.get("result_status") != "complete":
        raise RelayError("SCHEDULE_NOT_ELIGIBLE", "Only completely successful Jobs can become Schedules.")
    if not bool(job.get("replayable", 1)):
        raise RelayError("SCHEDULE_NOT_ELIGIBLE", "This Job did not save a replayable request.")
    raw = job.get("request_json")
    if not raw or raw == "{}":
        raise RelayError("SCHEDULE_INPUT_MISSING", "The Job request snapshot is missing.")
    try:
        request_data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise RelayError("SCHEDULE_INPUT_MISSING", "The Job request snapshot is invalid.") from None
    if not isinstance(request_data, dict):
        raise RelayError("SCHEDULE_INPUT_MISSING", "The Job request snapshot is invalid.")
    task = str(request_data.get("task") or job.get("task_text") or "").strip()
    if not task:
        raise RelayError("SCHEDULE_INPUT_MISSING", "The task text cannot be materialized.")
    request_data["task"] = task
    request_data["task_file"] = None
    worker = str(request_data.get("worker") or job.get("requested_worker") or "auto")
    if worker != "auto":
        try:
            registry.get_definition(worker)
        except KeyError:
            raise RelayError("SCHEDULE_AGENT_MISSING", f"The Agent is no longer registered: {worker}") from None
    request = JobRequest.from_dict(request_data)
    if not isinstance(request.attachments, list) or any(not isinstance(path, str) for path in request.attachments):
        raise RelayError("SCHEDULE_INPUT_INVALID", "Attachment paths are invalid.")
    return request


def materialize_snapshot(
    config: Config,
    schedule_id: str,
    task: str,
    attachments: list[str],
    *,
    max_bytes: int | None = None,
) -> ScheduleSnapshot:
    if not task.strip():
        raise RelayError("SCHEDULE_INPUT_MISSING", "The task text cannot be materialized.")
    root = _snapshot_root(config)
    ensure_dir(root)
    final_root = root / schedule_id
    if final_root.exists():
        raise RelayError("SCHEDULE_INPUT_INVALID", f"Schedule input snapshot already exists: {schedule_id}")
    limit = (
        max_bytes
        if max_bytes is not None
        else int(config.get("schedule_snapshot_max_total_bytes", config.get("artifact_max_total_bytes", 0)))
    )
    if limit < 1:
        raise RelayError("SCHEDULE_INPUT_INVALID", "The snapshot size limit must be positive.")
    names: set[str] = set()
    sources: list[tuple[Path, str, int, str]] = []
    total = 0
    for value in attachments:
        source = Path(value)
        if not source.exists() or not source.is_file():
            raise RelayError("SCHEDULE_INPUT_MISSING", f"Attachment not found: {source}")
        if _has_symlink_component(source):
            raise RelayError("SCHEDULE_PATH_NOT_ALLOWED", f"Symlink attachments are not allowed: {source}")
        name = source.name
        if not name or name in names or name in {"request.md", "attachments.json"}:
            raise RelayError("SCHEDULE_INPUT_INVALID", f"Attachment filename is duplicated or reserved: {name}")
        names.add(name)
        size, digest = _hash_file(source)
        total += size
        if total > limit:
            raise RelayError("SCHEDULE_INPUT_INVALID", "Schedule input attachments exceed the configured size limit.")
        sources.append((source, name, size, digest))

    temporary = root / f".{schedule_id}.tmp-{uuid.uuid4().hex}"
    stored_attachments: list[Path] = []
    try:
        ensure_dir(temporary)
        task_file = temporary / "request.md"
        task_file.write_text(task, encoding="utf-8")
        manifest_items: list[dict[str, Any]] = []
        for source, name, size, digest in sources:
            target = temporary / name
            shutil.copyfile(source, target)
            stored_attachments.append(target)
            manifest_items.append(
                {"source_path": str(safe_resolve(source)), "stored_path": name, "size": size, "sha256": digest}
            )
        manifest_path = temporary / "attachments.json"
        manifest_path.write_text(
            json.dumps({"schedule_id": schedule_id, "attachments": manifest_items}, indent=2), encoding="utf-8"
        )
        os.replace(temporary, final_root)
    except RelayError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        raise RelayError("SCHEDULE_INPUT_INVALID", f"Could not materialize Schedule inputs: {exc}") from exc

    return ScheduleSnapshot(
        schedule_id=schedule_id,
        root=final_root,
        task_file=final_root / "request.md",
        attachments=tuple(final_root / path.name for path in stored_attachments),
        manifest_path=final_root / "attachments.json",
    )


def load_snapshot(schedule_id: str, root: Path) -> ScheduleSnapshot:
    root = safe_resolve(root)
    task_file = root / "request.md"
    manifest_path = root / "attachments.json"
    if not root.is_dir() or not task_file.is_file() or not manifest_path.is_file():
        raise RelayError("SCHEDULE_INPUT_MISSING", f"Schedule input snapshot is incomplete: {schedule_id}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RelayError("SCHEDULE_INPUT_INVALID", f"Schedule input manifest is invalid: {schedule_id}") from exc
    attachments: list[Path] = []
    for item in manifest.get("attachments", []):
        path = safe_resolve(root / str(item.get("stored_path") or ""))
        if not is_within(path, root) or not path.is_file() or _has_symlink_component(path):
            raise RelayError("SCHEDULE_PATH_NOT_ALLOWED", f"Schedule input escapes its snapshot: {path}")
        attachments.append(path)
    return ScheduleSnapshot(schedule_id, root, task_file, tuple(attachments), manifest_path)


def build_scheduled_request(
    source: JobRequest,
    snapshot: ScheduleSnapshot,
    output_path: Path,
    artifact_path: Path,
) -> JobRequest:
    request = JobRequest.from_dict(source.to_dict())
    request.task = snapshot.task_file.read_text(encoding="utf-8")
    request.task_file = str(snapshot.task_file)
    request.attachments = [str(path) for path in snapshot.attachments]
    request.request_id = None
    request.force_new = True
    request.output_path = str(output_path)
    request.artifact_path = str(artifact_path)
    request.workspace = None
    request.caller = "schedule"
    return request


def schedule_output_paths(
    config: Config,
    schedule_id: str,
    run_id: str,
    scheduled_local: datetime,
    result_format: str,
    output_root: Path | None = None,
) -> tuple[Path, Path]:
    if scheduled_local.tzinfo is None:
        raise RelayError("SCHEDULE_PATH_NOT_ALLOWED", "Scheduled output time must include a timezone.")
    root = safe_resolve(output_root or config.home / "schedule-outputs" / schedule_id)
    folder = f"{scheduled_local.strftime('%Y-%m-%d_%H%M%z')}_{run_id[:12]}"
    run_root = safe_resolve(root / folder)
    if not is_within(run_root, root):
        raise RelayError("SCHEDULE_PATH_NOT_ALLOWED", "Schedule output path escapes its root.")
    suffix = ".json" if result_format == "json" else ".txt"
    return run_root / f"result{suffix}", run_root / "artifacts"
