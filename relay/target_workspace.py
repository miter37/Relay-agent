from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import RelayError
from .util import is_within, safe_resolve, sha256_file

_WRITE_INTENT = re.compile(
    r"\b(create|make|write|save|modify|edit|update|fix|refactor|add|delete|rename|copy|move)\b"
    r"|만들|생성|저장|수정|보완|추가|삭제|변경|작성|반영|복사|이동",
    re.IGNORECASE,
)
_QUOTED_WINDOWS_PATH = re.compile(r"[`'\"]([A-Za-z]:\\[^`'\"\r\n]+)[`'\"]")
_BARE_WINDOWS_PATH = re.compile(r"(?<![\w])([A-Za-z]:\\[^\s`'\"<>|?*]+)")
_QUOTED_POSIX_PATH = re.compile(r"[`'\"](/[^\r\n`'\"]+)[`'\"]")
_BARE_POSIX_PATH = re.compile(r"(?<![\w:/])(/[^\s`'\"<>|?*]+)")
_SKIPPED_DIRS = {".git", ".hg", ".svn"}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class TargetDelta:
    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]

    @property
    def changed(self) -> tuple[str, ...]:
        return (*self.added, *self.modified)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "added": list(self.added),
            "modified": list(self.modified),
            "deleted": list(self.deleted),
        }


@dataclass(frozen=True)
class TargetWorkspace:
    target: Path
    working_copy: Path
    existed: bool
    baseline: dict[str, str]


def _clean_candidate(value: str) -> str:
    candidate = value.rstrip(".,;:!?)]}，。；：！？")
    for suffix in ("에서", "으로", "로", "에", "의"):
        marker = f"\\{suffix}"
        if candidate.endswith(marker):
            candidate = candidate[: -len(suffix)]
            break
    return candidate


def task_target_candidates(task: str) -> list[str]:
    candidates = [
        *(_clean_candidate(match.group(1)) for match in _QUOTED_WINDOWS_PATH.finditer(task)),
        *(_clean_candidate(match.group(1)) for match in _QUOTED_POSIX_PATH.finditer(task)),
        *(_clean_candidate(match.group(1)) for match in _BARE_WINDOWS_PATH.finditer(task)),
        *(_clean_candidate(match.group(1)) for match in _BARE_POSIX_PATH.finditer(task)),
    ]
    distinct: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if any(longer.startswith(f"{candidate} ") for longer in candidates if longer != candidate):
            continue
        key = os.path.normcase(candidate)
        if key not in seen:
            seen.add(key)
            distinct.append(candidate)
    return distinct


def infer_target_path(task: str) -> str | None:
    if not _WRITE_INTENT.search(task):
        return None
    candidates = task_target_candidates(task)
    if len(candidates) > 1:
        raise RelayError(
            "TARGET_PATH_AMBIGUOUS",
            "The task names multiple filesystem paths. Select one Working folder in the GUI or pass --target.",
        )
    return candidates[0] if candidates else None


def resolve_target_path(value: str) -> Path:
    raw = Path(value).expanduser()
    target = safe_resolve(raw)
    if target.exists() and not target.is_dir():
        raise RelayError("TARGET_PATH_INVALID", f"Working folder must be a directory: {target}")
    if target == Path(target.anchor):
        raise RelayError("TARGET_PATH_INVALID", f"Filesystem roots cannot be used as a Working folder: {target}")
    return target


def validate_target_path(target: Path, relay_home: Path, reserved_paths: tuple[Path, ...]) -> None:
    if is_within(target, relay_home) or is_within(relay_home, target):
        raise RelayError("TARGET_PATH_NOT_ALLOWED", "Working folder cannot overlap Relay Home.")
    for reserved in reserved_paths:
        if is_within(target, reserved) or is_within(reserved, target):
            raise RelayError("TARGET_PATH_NOT_ALLOWED", f"Working folder overlaps a Relay delivery path: {reserved}")


def _manifest(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    if not root.exists():
        return files
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if name not in _SKIPPED_DIRS]
        for name in [*dirs, *names]:
            path = current_path / name
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            if path.is_symlink() or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise RelayError("TARGET_PATH_NOT_ALLOWED", f"Working folder cannot contain symbolic links: {path}")
        for name in names:
            path = current_path / name
            files[path.relative_to(root).as_posix()] = sha256_file(path)
    return files


def target_fingerprint(target: Path) -> str:
    manifest = _manifest(target)
    return "|".join(f"{name}:{digest}" for name, digest in sorted(manifest.items()))


def prepare_target_workspace(target: Path, working_copy: Path) -> TargetWorkspace:
    existed = target.exists()
    baseline = _manifest(target)
    if working_copy.exists():
        shutil.rmtree(working_copy)
    if existed:
        shutil.copytree(
            target,
            working_copy,
            symlinks=False,
            ignore=shutil.ignore_patterns(*_SKIPPED_DIRS),
        )
    else:
        working_copy.mkdir(parents=True)
    return TargetWorkspace(target, working_copy, existed, baseline)


def calculate_delta(workspace: TargetWorkspace) -> TargetDelta:
    final = _manifest(workspace.working_copy)
    baseline_names = set(workspace.baseline)
    final_names = set(final)
    return TargetDelta(
        added=tuple(sorted(final_names - baseline_names)),
        modified=tuple(
            sorted(name for name in baseline_names & final_names if workspace.baseline[name] != final[name])
        ),
        deleted=tuple(sorted(baseline_names - final_names)),
    )


def copy_delta_to_artifacts(workspace: TargetWorkspace, delta: TargetDelta, artifact_dir: Path) -> None:
    for relative in delta.changed:
        source = workspace.working_copy / Path(relative)
        destination = artifact_dir / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _verify_no_conflicts(workspace: TargetWorkspace, delta: TargetDelta) -> None:
    if workspace.existed and not workspace.target.is_dir():
        raise RelayError("TARGET_CONFLICT", f"Working folder was removed while the task ran: {workspace.target}")
    for relative in delta.added:
        if (workspace.target / Path(relative)).exists():
            raise RelayError("TARGET_CONFLICT", f"A file was created while the task ran: {relative}")
    for relative in (*delta.modified, *delta.deleted):
        current = workspace.target / Path(relative)
        if not current.is_file() or sha256_file(current) != workspace.baseline[relative]:
            raise RelayError("TARGET_CONFLICT", f"A source file changed while the task ran: {relative}")


def apply_delta(workspace: TargetWorkspace, delta: TargetDelta) -> None:
    if not workspace.existed:
        if workspace.target.exists():
            raise RelayError("TARGET_CONFLICT", f"Working folder was created while the task ran: {workspace.target}")
        workspace.target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{workspace.target.name}.relay-", dir=str(workspace.target.parent)))
        shutil.rmtree(temporary)
        try:
            shutil.copytree(workspace.working_copy, temporary)
            os.replace(temporary, workspace.target)
        except OSError as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise RelayError("TARGET_DELIVERY_FAILED", f"Could not create Working folder: {exc}") from exc
        return

    _verify_no_conflicts(workspace, delta)
    backup_root = Path(tempfile.mkdtemp(prefix=".relay-backup-", dir=str(workspace.target.parent)))
    applied: list[str] = []
    removed: list[str] = []
    try:
        for relative in (*delta.modified, *delta.deleted):
            source = workspace.target / Path(relative)
            backup = backup_root / Path(relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
        for relative in delta.changed:
            source = workspace.working_copy / Path(relative)
            destination = workspace.target / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.relay-tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            applied.append(relative)
        for relative in delta.deleted:
            (workspace.target / Path(relative)).unlink()
            removed.append(relative)
    except OSError as exc:
        for relative in reversed(applied):
            destination = workspace.target / Path(relative)
            backup = backup_root / Path(relative)
            if backup.is_file():
                shutil.copy2(backup, destination)
            else:
                destination.unlink(missing_ok=True)
        for relative in removed:
            backup = backup_root / Path(relative)
            destination = workspace.target / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)
        raise RelayError("TARGET_DELIVERY_FAILED", f"Could not update Working folder: {exc}") from exc
    finally:
        shutil.rmtree(backup_root, ignore_errors=True)
