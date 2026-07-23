from __future__ import annotations

import os
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters.generic import BUILTIN_WORKERS, validate_worker_id
from .config import Config
from .errors import RelayError
from .util import canonical_json, ensure_dir, json_dump, json_load, safe_resolve, sha256_bytes

MANIFEST_SCHEMA_VERSION = 1
_PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
ALLOWED_PLACEHOLDERS = frozenset(
    {
        "request_file",
        "result_file",
        "artifact_dir",
        "workspace",
        "schema_file",
        "task",
        "model",
        "profile",
        "job_id",
    }
)
_SHELL_FORMS = re.compile(r"(?:\x00|\r|\n|&&|\|\||[|;<>]|`|\$\()")


class AgentAppStore:
    """Atomic, file-backed storage for user-defined Agent App manifests."""

    def __init__(self, config: Config):
        self.config = config
        self.root = safe_resolve(config.home / "config" / "agent-apps")
        self.trash = self.root / ".trash"
        ensure_dir(self.root)

    def _path(self, agent_id: str) -> Path:
        validate_worker_id(agent_id)
        return self.root / f"{agent_id}.json"

    def get(self, agent_id: str) -> dict[str, Any] | None:
        value = json_load(self._path(agent_id))
        return value if isinstance(value, dict) else None

    def list(self) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.root.glob("*.json")):
            value = json_load(path)
            if isinstance(value, dict):
                values.append(value)
        return values

    def definition_hash(self, manifest: dict[str, Any]) -> str:
        runtime = {
            key: manifest.get(key)
            for key in (
                "schema_version",
                "agent_id",
                "executable",
                "argv",
                "input_mode",
                "result_mode",
                "result_formats",
                "supports_artifacts",
                "default_model",
                "model_list_argv",
                "model_list_parser",
                "model_arg",
                "safety",
            )
        }
        return sha256_bytes(canonical_json(runtime).encode("utf-8"))

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = self._normalize(payload)
        path = self._path(value["agent_id"])
        if path.exists() and json_load(path) is None:
            raise RelayError("AGENT_TEMPLATE_INVALID", f"Agent manifest is not valid JSON: {path}")
        json_dump(path, value)
        return value

    def delete(self, agent_id: str) -> bool:
        path = self._path(agent_id)
        if not path.exists():
            return False
        ensure_dir(self.trash)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.trash / f"{agent_id}-{stamp}.json"
        suffix = 1
        while target.exists():
            target = self.trash / f"{agent_id}-{stamp}-{suffix}.json"
            suffix += 1
        os.replace(path, target)
        return True

    def import_legacy(self) -> list[dict[str, Any]]:
        imported = []
        for agent_id, config in self.config.get("workers", {}).items():
            if agent_id in BUILTIN_WORKERS or self.get(agent_id) is not None:
                continue
            template = str(config.get("command_template") or "").strip()
            if not template:
                continue
            try:
                tokens = shlex.split(template)
            except ValueError:
                continue
            if tokens and tokens[0] == "{cli}":
                tokens = tokens[1:]
            value = self._normalize(
                {
                    "schema_version": MANIFEST_SCHEMA_VERSION,
                    "agent_id": agent_id,
                    "display_name": config.get("display_name") or agent_id.capitalize(),
                    "description": config.get("description") or "Imported from relay.toml",
                    "executable": config.get("command") or agent_id,
                    "argv": tokens,
                    "input_mode": "request_file" if "{request_file}" in template else "task_arg",
                    "result_mode": "result_file" if "{result_file}" in template else "stdout",
                    "result_formats": ["json", "txt"],
                    "supports_artifacts": True,
                    "default_model": config.get("default_model") or "",
                    "model_list_argv": [],
                    "model_list_parser": "lines",
                    "model_arg": ["--model", "{model}"],
                    "safety": {
                        "network": True,
                        "workspace_write": True,
                        "may_skip_permissions": False,
                        "env_names": [],
                    },
                    "enabled": False,
                }
            )
            self.save(value)
            imported.append(value)
        return imported

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise RelayError("INVALID_REQUEST", "Agent manifest must be an object")
        value = dict(payload)
        if value.get("schema_version", MANIFEST_SCHEMA_VERSION) != MANIFEST_SCHEMA_VERSION:
            raise RelayError("AGENT_TEMPLATE_INVALID", "Unsupported Agent manifest schema version")
        agent_id = str(value.get("agent_id") or "").strip()
        validate_worker_id(agent_id)
        executable = str(value.get("executable") or "").strip()
        if not executable:
            raise RelayError("AGENT_TEMPLATE_INVALID", "Agent executable is required")
        argv = value.get("argv")
        if not isinstance(argv, list) or any(not isinstance(item, str) or not item for item in argv):
            raise RelayError("AGENT_TEMPLATE_INVALID", "Agent argv must be a non-empty string list")
        placeholders = set()
        for token in [executable, *argv]:
            if _SHELL_FORMS.search(token):
                raise RelayError("AGENT_TEMPLATE_INVALID", "Shell operators and command substitution are not allowed")
            placeholders.update(_PLACEHOLDER_PATTERN.findall(token))
        unknown = placeholders - ALLOWED_PLACEHOLDERS
        if unknown:
            raise RelayError("AGENT_TEMPLATE_INVALID", f"Unknown Agent placeholder: {sorted(unknown)[0]}")
        input_mode = str(value.get("input_mode") or "request_file")
        if input_mode not in {"request_file", "stdin", "task_arg"}:
            raise RelayError("AGENT_TEMPLATE_INVALID", "input_mode must be request_file, stdin, or task_arg")
        result_mode = str(value.get("result_mode") or "result_file")
        if result_mode not in {"result_file", "stdout"}:
            raise RelayError("AGENT_TEMPLATE_INVALID", "result_mode must be result_file or stdout")
        if input_mode == "request_file" and "request_file" not in placeholders:
            raise RelayError("AGENT_TEMPLATE_INVALID", "request_file input requires {request_file}")
        if input_mode == "task_arg" and "task" not in placeholders:
            raise RelayError("AGENT_TEMPLATE_INVALID", "task_arg input requires {task}")
        if result_mode == "result_file" and "result_file" not in placeholders:
            raise RelayError("AGENT_TEMPLATE_INVALID", "result_file output requires {result_file}")
        formats = value.get("result_formats") or ["json"]
        if not isinstance(formats, list) or not formats or any(item not in {"json", "txt"} for item in formats):
            raise RelayError("AGENT_TEMPLATE_INVALID", "result_formats must contain json or txt")
        safety = value.get("safety") or {}
        if not isinstance(safety, dict):
            raise RelayError("AGENT_TEMPLATE_INVALID", "safety must be an object")
        env_names = safety.get("env_names") or []
        if not isinstance(env_names, list) or any(not isinstance(item, str) or not item for item in env_names):
            raise RelayError("AGENT_TEMPLATE_INVALID", "safety.env_names must be a string list")
        value.update(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "agent_id": agent_id,
                "display_name": str(value.get("display_name") or agent_id.capitalize()).strip(),
                "description": str(value.get("description") or "").strip(),
                "executable": executable,
                "argv": argv,
                "input_mode": input_mode,
                "result_mode": result_mode,
                "result_formats": list(dict.fromkeys(formats)),
                "supports_artifacts": bool(value.get("supports_artifacts", False)),
                "default_model": str(value.get("default_model") or ""),
                "model_list_argv": list(value.get("model_list_argv") or []),
                "model_list_parser": str(value.get("model_list_parser") or "lines"),
                "model_arg": list(value.get("model_arg") or []),
                "safety": {
                    "network": bool(safety.get("network", False)),
                    "workspace_write": bool(safety.get("workspace_write", False)),
                    "may_skip_permissions": bool(safety.get("may_skip_permissions", False)),
                    "env_names": list(dict.fromkeys(env_names)),
                },
                "enabled": bool(value.get("enabled", False)),
            }
        )
        return value
