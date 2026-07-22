from __future__ import annotations

import os
import shutil
from pathlib import Path

from .errors import RelayError
from .util import ensure_dir, sha256_file


def _prepare_target(path: Path, overwrite: bool) -> None:
    ensure_dir(path.parent)
    if path.exists() and not overwrite:
        raise RelayError("OUTPUT_EXISTS", f"Target already exists: {path}")


def atomic_deliver_file(source: Path, target: Path, overwrite: bool = False) -> None:
    if not source.is_file():
        raise RelayError("DELIVERY_FAILED", f"Staged result does not exist: {source}")
    _prepare_target(target, overwrite)
    temp_target = target.with_name(target.name + ".relay-partial")
    if temp_target.exists():
        temp_target.unlink()
    try:
        if source.drive == target.drive and source.anchor == target.anchor:
            # os.replace is atomic on the same filesystem.
            if overwrite and target.exists():
                target.unlink()
            os.replace(source, target)
        else:
            shutil.copy2(source, temp_target)
            if sha256_file(source) != sha256_file(temp_target):
                raise RelayError("DELIVERY_FAILED", "Cross-volume result hash mismatch")
            if overwrite and target.exists():
                target.unlink()
            os.replace(temp_target, target)
    except RelayError:
        raise
    except OSError as exc:
        raise RelayError("DELIVERY_FAILED", f"Could not deliver result: {exc}", True) from exc
    finally:
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)


def atomic_deliver_directory(source: Path, target: Path, overwrite: bool = False) -> None:
    ensure_dir(target.parent)
    if target.exists():
        if not overwrite:
            raise RelayError("OUTPUT_EXISTS", f"Artifact directory already exists: {target}")
        shutil.rmtree(target)
    temp_target = target.with_name(target.name + ".relay-partial")
    if temp_target.exists():
        shutil.rmtree(temp_target)
    try:
        shutil.copytree(source, temp_target, symlinks=False)
        os.replace(temp_target, target)
    except OSError as exc:
        raise RelayError("DELIVERY_FAILED", f"Could not deliver artifacts: {exc}", True) from exc
    finally:
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
