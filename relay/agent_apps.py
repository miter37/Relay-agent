from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters.generic import BUILTIN_WORKERS, validate_worker_id
from .config import Config
from .errors import RelayError
from .models import AdapterSpec
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
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
                "model_list_timeout_seconds",
                "model_arg",
                "safety",
            )
        }
        return sha256_bytes(canonical_json(runtime).encode("utf-8"))

    def save(self, payload: dict[str, Any], *, allow_enabled: bool = False) -> dict[str, Any]:
        value = self._normalize(payload)
        if value["enabled"] and not allow_enabled:
            value["enabled"] = False
            value["status"] = "needs_test"
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
                    "model_list_timeout_seconds": 30,
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
        model_list_argv = value.get("model_list_argv") or []
        model_arg = value.get("model_arg") or []
        model_list_timeout_seconds = value.get("model_list_timeout_seconds", 30)
        if not isinstance(model_list_argv, list) or any(
            not isinstance(item, str) or not item for item in model_list_argv
        ):
            raise RelayError("AGENT_TEMPLATE_INVALID", "model_list_argv must be a string list")
        if not isinstance(model_arg, list) or any(not isinstance(item, str) or not item for item in model_arg):
            raise RelayError("AGENT_TEMPLATE_INVALID", "model_arg must be a string list")
        if not isinstance(model_list_timeout_seconds, int) or not 1 <= model_list_timeout_seconds <= 300:
            raise RelayError("AGENT_TEMPLATE_INVALID", "model_list_timeout_seconds must be between 1 and 300")
        for token in [executable, *argv, *model_list_argv, *model_arg]:
            if _SHELL_FORMS.search(token):
                raise RelayError("AGENT_TEMPLATE_INVALID", "Shell operators and command substitution are not allowed")
            placeholders.update(_PLACEHOLDER_PATTERN.findall(token))
        unknown = placeholders - ALLOWED_PLACEHOLDERS
        if unknown:
            raise RelayError("AGENT_TEMPLATE_INVALID", f"Unknown Agent placeholder: {sorted(unknown)[0]}")
        if _PLACEHOLDER_PATTERN.search(executable):
            raise RelayError("AGENT_TEMPLATE_INVALID", "Agent executable cannot contain placeholders")
        if any(_PLACEHOLDER_PATTERN.search(token) for token in model_list_argv):
            raise RelayError("AGENT_TEMPLATE_INVALID", "model_list_argv cannot contain placeholders")
        argv_model_count = sum(token.count("{model}") for token in argv)
        model_arg_count = sum(token.count("{model}") for token in model_arg)
        if argv_model_count and model_arg:
            raise RelayError("AGENT_TEMPLATE_INVALID", "Use {model} in argv or model_arg, not both")
        if model_arg and model_arg_count != 1:
            raise RelayError("AGENT_TEMPLATE_INVALID", "model_arg must contain {model} exactly once")
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
        model_list_parser = str(value.get("model_list_parser") or "lines")
        if model_list_parser not in {"lines", "json"}:
            raise RelayError("AGENT_TEMPLATE_INVALID", "model_list_parser must be lines or json")
        formats = value.get("result_formats") or ["json"]
        if not isinstance(formats, list) or not formats or any(item not in {"json", "txt"} for item in formats):
            raise RelayError("AGENT_TEMPLATE_INVALID", "result_formats must contain json or txt")
        safety = value.get("safety") or {}
        if not isinstance(safety, dict):
            raise RelayError("AGENT_TEMPLATE_INVALID", "safety must be an object")
        env_names = safety.get("env_names") or []
        if not isinstance(env_names, list) or any(not isinstance(item, str) or not item for item in env_names):
            raise RelayError("AGENT_TEMPLATE_INVALID", "safety.env_names must be a string list")
        if any(not _ENV_NAME_PATTERN.match(item) for item in env_names):
            raise RelayError("AGENT_TEMPLATE_INVALID", "safety.env_names contains an invalid environment variable name")
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
                "model_list_argv": list(model_list_argv),
                "model_list_parser": model_list_parser,
                "model_list_timeout_seconds": model_list_timeout_seconds,
                "model_arg": list(model_arg),
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


class AgentAppService:
    """Lifecycle operations shared by the daemon API, CLI, and GUI."""

    def __init__(self, config: Config, db, engine):
        self.config = config
        self.db = db
        self.engine = engine
        self.store = AgentAppStore(config)
        self._pending_tests: dict[str, dict[str, Any]] = {}
        self._pending_tests_lock = threading.Lock()

    def list(self) -> list[dict[str, Any]]:
        return [self._public(item) for item in self.store.list()]

    def show(self, agent_id: str) -> dict[str, Any]:
        manifest = self.store.get(agent_id)
        if manifest is None:
            raise RelayError("AGENT_NOT_FOUND", f"Agent App not found: {agent_id}")
        return self._public(manifest)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        test_token = str(payload.pop("test_token", "") or "")
        agent_id = str(payload.get("agent_id") or "")
        if self.store.get(agent_id) is not None:
            raise RelayError("AGENT_DUPLICATE", f"Agent App already exists: {agent_id}")
        value = self.store._normalize(payload)
        pending = self._matching_test(test_token, "create", value) if test_token else None
        value["enabled"] = False
        value["status"] = "ready" if pending else "needs_test"
        saved = self.store.save(value)
        if pending:
            self._promote_test(test_token, saved, pending)
        return self._public(saved)

    def update(self, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        test_token = str(payload.pop("test_token", "") or "")
        existing = self.store.get(agent_id)
        if existing is None:
            raise RelayError("AGENT_NOT_FOUND", f"Agent App not found: {agent_id}")
        if "agent_id" in payload and payload["agent_id"] != agent_id:
            raise RelayError("AGENT_INVALID_NAME", "Agent ID cannot be changed")
        merged = {**existing, **payload, "agent_id": agent_id}
        normalized = self.store._normalize(merged)
        pending = self._matching_test(test_token, "update", normalized) if test_token else None
        old_hash = self.store.definition_hash(existing)
        new_hash = self.store.definition_hash(normalized)
        if old_hash != new_hash:
            normalized["enabled"] = False
            normalized["status"] = "ready" if pending else "needs_test"
        elif pending:
            normalized["status"] = "ready"
        saved = self.store.save(normalized, allow_enabled=old_hash == new_hash and bool(existing.get("enabled")))
        if pending:
            self._promote_test(test_token, saved, pending)
        return self._public(saved)

    def test_manifest(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "")
        manifest = payload.get("manifest")
        if mode not in {"create", "update"} or not isinstance(manifest, dict):
            raise RelayError("INVALID_REQUEST", "mode and manifest are required")
        normalized = self.store._normalize({**manifest, "enabled": False, "status": "needs_test"})
        agent_id = normalized["agent_id"]
        existing = self.store.get(agent_id)
        if mode == "create" and existing is not None:
            raise RelayError("AGENT_DUPLICATE", f"Agent App already exists: {agent_id}")
        if mode == "update" and existing is None:
            raise RelayError("AGENT_NOT_FOUND", f"Agent App not found: {agent_id}")
        definition_hash = self.store.definition_hash(normalized)
        from .adapters.generic import GenericCLIAdapter
        from .doctor import Doctor

        with tempfile.TemporaryDirectory(prefix="relay-agent-test-") as spec_root:
            adapter_config = {**normalized, "_definition_hash": definition_hash, "require_deep_doctor": True}
            adapter = GenericCLIAdapter(adapter_config, Path(spec_root), name=agent_id)
            spec = Doctor(self.config, self.db).audit_adapter(adapter, deep=True, record_audit=False)
        result = spec.to_dict()
        response = {"test": result, "manifest_hash": definition_hash, "test_token": None}
        if spec.status == "healthy":
            token = secrets.token_urlsafe(24)
            with self._pending_tests_lock:
                self._prune_pending_tests()
                self._pending_tests[token] = {
                    "mode": mode,
                    "agent_id": agent_id,
                    "manifest_hash": definition_hash,
                    "expires_at": time.monotonic() + 600,
                    "spec": spec.to_dict(),
                }
            response["test_token"] = token
        return response

    def _prune_pending_tests(self) -> None:
        now = time.monotonic()
        for token in [key for key, value in self._pending_tests.items() if value["expires_at"] <= now]:
            self._pending_tests.pop(token, None)

    def _matching_test(self, token: str, mode: str, manifest: dict[str, Any]) -> dict[str, Any]:
        with self._pending_tests_lock:
            self._prune_pending_tests()
            pending = self._pending_tests.get(token)
            if (
                not pending
                or pending["mode"] != mode
                or pending["agent_id"] != manifest["agent_id"]
                or pending["manifest_hash"] != self.store.definition_hash(manifest)
            ):
                raise RelayError("AGENT_TEST_REQUIRED", "Run a successful deep test for the current Agent definition")
            return dict(pending)

    def _promote_test(self, token: str, manifest: dict[str, Any], pending: dict[str, Any]) -> None:
        adapter = self.engine.agent_registry.get_adapter(manifest["agent_id"])
        spec = AdapterSpec.from_dict(pending["spec"])
        adapter.save_spec(spec)
        self.db.add_audit(
            manifest["agent_id"],
            spec.version,
            "deep",
            "passed",
            spec.details,
            adapter.spec_hash(spec),
        )
        with self._pending_tests_lock:
            self._pending_tests.pop(token, None)

    def set_enabled(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        manifest = self.store.get(agent_id)
        if manifest is None:
            raise RelayError("AGENT_NOT_FOUND", f"Agent App not found: {agent_id}")
        if enabled:
            self.engine.agent_registry.get_adapter(agent_id).require_verified()
            manifest["enabled"] = True
            manifest["status"] = "ready"
        else:
            manifest["enabled"] = False
            manifest["status"] = "disabled"
        return self._public(self.store.save(manifest, allow_enabled=enabled))

    def test(self, agent_id: str) -> dict[str, Any]:
        manifest = self.store.get(agent_id)
        if manifest is None:
            raise RelayError("AGENT_NOT_FOUND", f"Agent App not found: {agent_id}")
        from .doctor import Doctor

        report = Doctor(self.config, self.db).audit([agent_id], deep=True)
        item = (report.get("workers") or [{}])[0]
        healthy = item.get("status") == "healthy"
        manifest["status"] = "ready" if healthy else "needs_test"
        if not healthy:
            manifest["enabled"] = False
        self.store.save(manifest, allow_enabled=healthy and bool(manifest.get("enabled")))
        return {"agent": self._public(manifest), "test": item}

    def delete(self, agent_id: str) -> bool:
        if self.store.get(agent_id) is None:
            raise RelayError("AGENT_NOT_FOUND", f"Agent App not found: {agent_id}")
        in_use: list[str] = []
        for schedule in self.db.list_schedules():
            if not schedule.get("enabled") or schedule.get("deleted_at"):
                continue
            source = self.db.get_job(schedule["source_job_id"])
            try:
                request = json.loads(source.get("request_json") or "{}") if source else {}
            except json.JSONDecodeError:
                request = {}
            if isinstance(request, dict) and request.get("worker") == agent_id:
                in_use.append(schedule["schedule_id"])
        if in_use:
            raise RelayError(
                "AGENT_IN_USE",
                f"Agent App is used by active Schedules: {', '.join(in_use)}",
                details={"schedule_ids": in_use},
            )
        return self.store.delete(agent_id)

    def _public(self, manifest: dict[str, Any]) -> dict[str, Any]:
        value = dict(manifest)
        value["manifest_hash"] = self.store.definition_hash(manifest)
        value.setdefault("status", "disabled" if not manifest.get("enabled") else "needs_test")
        value.pop("_definition_hash", None)
        return value
