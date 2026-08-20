from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any

from ..errors import RelayError
from ..util import safe_resolve

_FTS_TOKEN = re.compile(r"[\w\-]+", re.UNICODE)
_TEXT_SUFFIXES = {
    ".bash",
    ".bat",
    ".cfg",
    ".cmd",
    ".conf",
    ".css",
    ".csv",
    ".diff",
    ".html",
    ".htm",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".markdown",
    ".ndjson",
    ".patch",
    ".ps1",
    ".py",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}


def normalize_limit(value: int, *, default: int = 20, maximum: int = 100) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise RelayError("INVALID_REQUEST", "Search limit must be an integer.") from None
    if limit < 1 or limit > maximum:
        raise RelayError("INVALID_REQUEST", f"Search limit must be between 1 and {maximum}.")
    return limit or default


def normalize_max_bytes(value: int, *, default: int = 65536, maximum: int = 20 * 1024 * 1024) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        raise RelayError("INVALID_REQUEST", "max_bytes must be an integer.") from None
    if size < 1 or size > maximum:
        raise RelayError("INVALID_REQUEST", f"max_bytes must be between 1 and {maximum}.")
    return size or default


def fts_query(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "*"
    tokens = _FTS_TOKEN.findall(text)
    if not tokens:
        raise RelayError("INVALID_REQUEST", "Search query contains no searchable terms.")
    return " AND ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)


def snippet(value: str | None, limit: int = 240) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _read_text(path: Path, max_bytes: int) -> str | None:
    if path.suffix.casefold() not in _TEXT_SUFFIXES:
        return None
    try:
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def artifact_search_content(artifact: dict[str, Any], *, max_bytes: int) -> tuple[str | None, bool]:
    path = safe_resolve(Path(str(artifact.get("final_path") or "")))
    if not path.is_file():
        return None, False
    return _read_text(path, max_bytes), True


def result_summary(job: dict[str, Any], *, max_bytes: int = 65536) -> str | None:
    path = safe_resolve(Path(str(job.get("output_path") or "")))
    if not path.is_file():
        return None
    text = _read_text(path, max_bytes)
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return snippet(text)
    if isinstance(value, dict):
        return snippet(value.get("answer") or value.get("summary") or text)
    return snippet(text)


def artifact_mime(artifact: dict[str, Any]) -> str | None:
    return artifact.get("mime_type") or mimetypes.guess_type(str(artifact.get("relative_path") or ""))[0]
