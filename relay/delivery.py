from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from .errors import RelayError
from .util import ensure_dir, sha256_file


def _prepare_target(path: Path, overwrite: bool) -> None:
    ensure_dir(path.parent)
    if path.exists() and not overwrite:
        raise RelayError("OUTPUT_EXISTS", f"Target already exists: {path}")


def _copy_file_to_temp(source: Path, target: Path) -> Path:
    if not source.is_file():
        raise RelayError("DELIVERY_FAILED", f"Staged result does not exist: {source}")
    temp = target.with_name(f".{target.name}.relay-tmp-{uuid.uuid4().hex}")
    try:
        shutil.copy2(source, temp)
        if sha256_file(source) != sha256_file(temp):
            raise RelayError("DELIVERY_FAILED", "Cross-volume result hash mismatch")
        return temp
    except RelayError:
        temp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise RelayError("DELIVERY_FAILED", f"Could not stage result: {exc}", True) from exc


def _copy_directory_to_temp(source: Path, target: Path) -> Path:
    if not source.is_dir():
        raise RelayError("DELIVERY_FAILED", f"Staged artifacts do not exist: {source}")
    temp = target.with_name(f".{target.name}.relay-tmp-{uuid.uuid4().hex}")
    try:
        shutil.copytree(source, temp, symlinks=False)
        return temp
    except OSError as exc:
        shutil.rmtree(temp, ignore_errors=True)
        raise RelayError("DELIVERY_FAILED", f"Could not stage artifacts: {exc}", True) from exc


def _commit_pair(
    staged_file: Path,
    target_file: Path,
    staged_dir: Path,
    target_dir: Path,
    overwrite: bool,
) -> None:
    backup_file = target_file.with_name(f".{target_file.name}.relay-backup-{uuid.uuid4().hex}")
    backup_dir = target_dir.with_name(f".{target_dir.name}.relay-backup-{uuid.uuid4().hex}")
    moved_file_backup = moved_dir_backup = False
    installed_file = installed_dir = False
    try:
        if target_file.exists():
            os.replace(target_file, backup_file)
            moved_file_backup = True
        if target_dir.exists():
            os.replace(target_dir, backup_dir)
            moved_dir_backup = True
        os.replace(staged_file, target_file)
        installed_file = True
        os.replace(staged_dir, target_dir)
        installed_dir = True
    except OSError as exc:
        if installed_dir and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if installed_file and target_file.exists():
            target_file.unlink(missing_ok=True)
        if moved_dir_backup and backup_dir.exists():
            os.replace(backup_dir, target_dir)
        if moved_file_backup and backup_file.exists():
            os.replace(backup_file, target_file)
        raise RelayError("DELIVERY_FAILED", f"Could not commit result and artifacts: {exc}", True) from exc
    finally:
        if backup_file.exists():
            backup_file.unlink(missing_ok=True)
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def atomic_deliver_pair(
    source_file: Path,
    target_file: Path,
    source_dir: Path,
    target_dir: Path,
    overwrite: bool = False,
) -> None:
    _prepare_target(target_file, overwrite)
    _prepare_target(target_dir, overwrite)
    staged_file = staged_dir = None
    try:
        staged_file = _copy_file_to_temp(source_file, target_file)
        staged_dir = _copy_directory_to_temp(source_dir, target_dir)
        _commit_pair(staged_file, target_file, staged_dir, target_dir, overwrite)
        staged_file = staged_dir = None
    finally:
        if staged_file and staged_file.exists():
            staged_file.unlink(missing_ok=True)
        if staged_dir and staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)


def atomic_deliver_file(source: Path, target: Path, overwrite: bool = False) -> None:
    _prepare_target(target, overwrite)
    staged = _copy_file_to_temp(source, target)
    try:
        os.replace(staged, target)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise RelayError("DELIVERY_FAILED", f"Could not deliver result: {exc}", True) from exc


def atomic_deliver_directory(source: Path, target: Path, overwrite: bool = False) -> None:
    _prepare_target(target, overwrite)
    staged = _copy_directory_to_temp(source, target)
    try:
        backup = target.with_name(f".{target.name}.relay-backup-{uuid.uuid4().hex}")
        moved_backup = False
        try:
            if target.exists():
                os.replace(target, backup)
                moved_backup = True
            os.replace(staged, target)
            staged = None
        except OSError as exc:
            if moved_backup and backup.exists():
                os.replace(backup, target)
            raise RelayError("DELIVERY_FAILED", f"Could not deliver artifacts: {exc}", True) from exc
        finally:
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
    finally:
        if staged and staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
