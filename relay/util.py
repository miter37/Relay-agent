from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def local_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def new_job_id() -> str:
    ms = int(time.time() * 1000)
    raw = ms.to_bytes(6, "big") + secrets.token_bytes(10)
    bits = int.from_bytes(raw, "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[bits & 31])
        bits >>= 5
    return "".join(reversed(out))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def task_hash(task: str, attachments: Iterable[str], profile: str, worker: str, result_format: str) -> str:
    payload: dict[str, Any] = {
        "task": " ".join(task.split()),
        "profile": profile,
        "worker": worker,
        "format": result_format,
        "attachments": [],
    }
    for item in attachments:
        p = Path(item)
        payload["attachments"].append(
            {
                "name": p.name,
                "sha256": sha256_file(p) if p.is_file() else None,
            }
        )
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def json_dump(path: Path, value: Any) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def json_load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError:
        return Path(os.path.abspath(os.path.expanduser(str(path))))


def is_within(path: Path, root: Path) -> bool:
    p = os.path.normcase(str(safe_resolve(path)))
    r = os.path.normcase(str(safe_resolve(root)))
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:
        return False


def human_size(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f}{unit}"
        amount /= 1024
    return f"{value}B"


def redact_env(env: dict[str, str]) -> dict[str, str]:
    blocked = ("TOKEN", "KEY", "SECRET", "PASSWORD", "COOKIE", "AUTH")
    return {k: ("<redacted>" if any(x in k.upper() for x in blocked) else v) for k, v in env.items()}


def entrypoint_command(extra: list[str]) -> list[str]:
    entry = os.environ.get("RELAY_ENTRYPOINT")
    if entry:
        if entry.lower().endswith((".pyz", ".py")):
            return [sys.executable, entry, *extra]
        return [entry, *extra]
    argv0 = Path(sys.argv[0])
    for candidate in (argv0, Path(__file__)):
        marker = ".pyz/"
        text = str(candidate)
        if marker in text:
            archive = Path(text.split(marker, 1)[0] + ".pyz")
            if archive.exists():
                return [sys.executable, str(archive.resolve()), *extra]
    if argv0.suffix.lower() in {".pyz", ".py"} and argv0.exists():
        if argv0.name == "__main__.py":
            return [sys.executable, "-m", "relay", *extra]
        return [sys.executable, str(argv0.resolve()), *extra]
    return [sys.executable, "-m", "relay", *extra]


def random_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def which(command: str) -> str | None:
    if os.path.isabs(command) and Path(command).exists():
        return command
    return shutil.which(command)
