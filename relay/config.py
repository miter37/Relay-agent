from __future__ import annotations

import os
import platform
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

from .util import ensure_dir, safe_resolve

DEFAULTS: dict[str, Any] = {
    "default_worker": "claude",
    "fallback_order": ["codex"],
    "default_format": "json",
    "default_profile": "web-research",
    "timeout_seconds": 1200,
    "soft_stall_seconds": 120,
    "hard_stall_seconds": 300,
    "poll_interval_seconds": 2,
    "max_concurrent_jobs": 2,
    "max_concurrent_per_worker": 1,
    "schedule_poll_interval_seconds": 1,
    "fallback_enabled": True,
    "history_mode": "metadata",
    "store_replayable_requests": True,
    "soft_dedup_window_minutes": 30,
    "soft_dedup_hermes_action": "reuse",
    "soft_dedup_human_action": "warn",
    "machine_stderr": False,
    "allow_manual_outside_roots": True,
    "service_isolation_acknowledged": False,
    "daemon_host": "127.0.0.1",
    "daemon_port": 47831,
    "daemon_auto_start": True,
    "autostart_enabled": False,
    "result_max_bytes": 20 * 1024 * 1024,
    "artifact_max_total_bytes": 1024 * 1024 * 1024,
    "artifact_max_files": 200,
    "retention_days": 30,  # legacy fallback
    "cleanup_enabled": True,
    "cleanup_interval_hours": 24,
    "cleanup_run_on_daemon_start": True,
    "cleanup_remove_empty_parents": True,
    "cleanup_remove_orphans": True,
    "cleanup_orphan_days": 7,
    "schedule_retention_enabled": True,
    "schedule_retention_interval_hours": 24,
    "schedule_retention_run_on_daemon_start": True,
    "retention_days_completed": 7,
    "retention_days_partial": 14,
    "retention_days_failed": 30,
    "retention_days_cancelled": 14,
    "workers": {
        "claude": {
            "enabled": True,
            "command": "claude",
            "require_deep_doctor": True,
            "default_model": "sonnet",
            "max_turns": 30,
            "max_budget_usd": 5.0,
            "tools": "WebSearch,WebFetch,Read,Write,Glob,Grep",
            "disallowed_tools": "mcp__*",
        },
        "codex": {
            "enabled": True,
            "command": "codex",
            "require_deep_doctor": True,
            "default_model": "",
            "sandbox": "workspace-write",
            "approval": "never",
            "live_search": True,
            "unsafe_yolo": False,
        },
        "antigravity": {
            "enabled": False,
            "command": "agy",
            "require_deep_doctor": True,
            "default_model": "",
            "dangerously_skip_permissions": True,
            "enable_only_if_security_verified": True,
            "security_verified": False,
        },
    },
}


def default_home() -> Path:
    override = os.environ.get("RELAY_HOME")
    if override:
        return safe_resolve(Path(override))
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or str(Path.home())
        return safe_resolve(Path(base) / "Relay")
    if platform.system() == "Darwin":
        return safe_resolve(Path.home() / "Library" / "Application Support" / "Relay")
    # Keep the established Linux path for backward compatibility.
    return safe_resolve(Path.home() / ".relay")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return '""'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _toml_dump(data: dict[str, Any]) -> str:
    root_lines: list[str] = []
    table_lines: list[str] = []

    def emit_table(prefix: str, table: dict[str, Any]) -> None:
        scalars: list[tuple[str, Any]] = []
        nested: list[tuple[str, dict[str, Any]]] = []
        for key, value in table.items():
            if isinstance(value, dict):
                nested.append((key, value))
            else:
                scalars.append((key, value))
        if prefix:
            table_lines.append(f"\n[{prefix}]")
        target = table_lines if prefix else root_lines
        for key, value in scalars:
            if isinstance(value, list):
                target.append(f"{key} = [{', '.join(_toml_scalar(v) for v in value)}]")
            else:
                target.append(f"{key} = {_toml_scalar(value)}")
        for key, child in nested:
            emit_table(f"{prefix}.{key}" if prefix else key, child)

    emit_table("", data)
    return "\n".join(root_lines + table_lines).strip() + "\n"


class Config:
    def __init__(self, home: Path | None = None):
        self.home = safe_resolve(home or default_home())
        self.config_dir = self.home / "config"
        self.path = self.config_dir / "relay.toml"
        self.data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        ensure_dir(self.config_dir)
        loaded: dict[str, Any] = {}
        if self.path.exists():
            with self.path.open("rb") as f:
                loaded = tomllib.load(f)
        self.data = _deep_merge(DEFAULTS, loaded)
        self._apply_paths()

    def _apply_paths(self) -> None:
        defaults = {
            "result_root": str(self.home / "results"),
            "artifact_root": str(self.home / "artifacts"),
            "workspace_root": str(self.home / "workspace"),
            "staging_root": str(self.home / "staging"),
            "log_root": str(self.home / "logs"),
            "request_root": str(self.home / "requests"),
            "adapter_spec_root": str(self.home / "adapter-specs"),
            "runtime_root": str(self.home / "runtime"),
            "database_path": str(self.home / "relay.db"),
            "allowed_input_roots": [str(self.home / "input"), str(self.home / "requests")],
            "allowed_output_roots": [str(self.home / "results")],
            "allowed_artifact_roots": [str(self.home / "artifacts")],
        }
        for key, value in defaults.items():
            self.data.setdefault(key, value)

    def init(self, force: bool = False) -> Path:
        ensure_dir(self.config_dir)
        if not self.path.exists() or force:
            self.path.write_text(_toml_dump(self.data), encoding="utf-8")
        for key in (
            "result_root",
            "artifact_root",
            "workspace_root",
            "staging_root",
            "log_root",
            "request_root",
            "adapter_spec_root",
            "runtime_root",
        ):
            ensure_dir(Path(self.data[key]))
        for root in self.data.get("allowed_input_roots", []):
            ensure_dir(Path(root))
        return self.path

    def save(self) -> None:
        ensure_dir(self.config_dir)
        self.path.write_text(_toml_dump(self.data), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        cur = self.data
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
        self.save()

    def worker(self, name: str) -> dict[str, Any]:
        return deepcopy(self.data.get("workers", {}).get(name, {}))

    def path_value(self, key: str) -> Path:
        return safe_resolve(Path(str(self.data[key])))
