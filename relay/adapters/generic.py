from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from ..errors import RelayError
from ..model_catalog import DiscoveredModel, ModelCatalog
from .base import Adapter, AdapterContext

BUILTIN_WORKERS = frozenset({"claude", "codex", "antigravity"})

KNOWN_PLACEHOLDERS = frozenset({"cli", "request_file", "result_file", "artifact_dir", "model"})

_PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_WORKER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
_SAFE_ENV_NAMES = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SystemRoot",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}


def validate_worker_id(worker_id: str) -> None:
    if not worker_id or not _WORKER_ID_PATTERN.match(worker_id):
        raise RelayError(
            "AGENT_INVALID_NAME",
            "Worker ID must start with a lowercase letter and contain only lowercase letters, digits, '_' or '-'.",
        )
    if worker_id in BUILTIN_WORKERS:
        raise RelayError(
            "AGENT_BUILTIN",
            f"'{worker_id}' is a built-in worker and cannot be re-registered with add-agent.",
        )


def validate_command_template(template: str) -> None:
    if not template or not template.strip():
        raise RelayError(
            "AGENT_TEMPLATE_INVALID",
            "Command template must not be empty.",
        )
    for match in _PLACEHOLDER_PATTERN.finditer(template):
        name = match.group(1)
        if name not in KNOWN_PLACEHOLDERS:
            raise RelayError(
                "AGENT_TEMPLATE_INVALID",
                f"Unknown placeholder '{{{name}}}' in command template. "
                f"Known placeholders: {', '.join('{' + p + '}' for p in sorted(KNOWN_PLACEHOLDERS))}.",
            )


def render_command_template(template: str, values: dict[str, str]) -> list[str]:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise RelayError(
                "AGENT_TEMPLATE_INVALID",
                f"Missing value for placeholder '{{{name}}}'.",
            )
        return shlex.quote(str(values[name]))

    rendered = _PLACEHOLDER_PATTERN.sub(replace, template)
    return shlex.split(rendered)


class GenericCLIAdapter(Adapter):
    """Adapter for a worker registered at runtime via 'relay add-agent'.

    The user supplies a command template (e.g.
    '{cli} exec --prompt {request_file} --output {result_file}') at registration
    time. The template is rendered per job by substituting the standard set of
    Relay placeholders. The adapter delegates shallow audit / doctor behavior
    to BaseAdapter; execution goes through the standard AdapterContext.
    """

    name = ""
    command_name = ""

    def __init__(
        self,
        worker_config: dict[str, Any],
        spec_root: Path,
        *,
        name: str | None = None,
    ):
        super().__init__(worker_config, spec_root)
        if name:
            self.name = name
        elif self.name:
            pass
        else:
            self.name = str(worker_config.get("worker_id") or worker_config.get("name") or "agent")
        self.manifest_mode = "argv" in worker_config or "executable" in worker_config
        self.command_name = str(
            worker_config.get("executable") or worker_config.get("command") or self.command_name or self.name
        )
        self.template = str(worker_config.get("command_template", ""))
        self.argv = [str(item) for item in worker_config.get("argv", [])]
        self.input_mode = str(worker_config.get("input_mode") or "request_file")
        self.result_mode = str(worker_config.get("result_mode") or "result_file")
        self.model_list_argv = [str(item) for item in worker_config.get("model_list_argv", [])]
        self.model_list_parser = str(worker_config.get("model_list_parser") or "lines")
        self.model_list_timeout_seconds = int(worker_config.get("model_list_timeout_seconds", 30))
        self.model_arg = [str(item) for item in worker_config.get("model_arg", [])]

    def detect_capabilities(self, help_text: str) -> dict[str, Any]:
        return {
            "generic_cli_template": True,
            "template": self.template,
            "warning": (
                "Capabilities are derived from the user-supplied command template. "
                "Deep doctor is authoritative for execution verification."
            ),
        }

    def permission_mode(self) -> str:
        return "user-defined"

    def sandbox_mode(self) -> str:
        return "external-workspace"

    def build_command(self, ctx: AdapterContext) -> tuple[list[str], bytes | None, dict[str, str]]:
        executable = self.executable()
        if not executable:
            raise RelayError(
                "WORKER_NOT_INSTALLED",
                f"{self.name} executable not found: {self.command_name}",
            )
        if self.manifest_mode:
            if not self.argv:
                raise RelayError("AGENT_TEMPLATE_INVALID", f"{self.name} has no argv configured")
            model = ctx.model or ctx.config.get("default_model") or self.worker_config.get("default_model") or ""
            values = self._manifest_values(ctx, model)
            args = [executable, *self._render_tokens(self.argv, values)]
            if model and self.model_arg:
                args.extend(self._render_tokens(self.model_arg, values))
            stdin_bytes = ctx.request_file.read_bytes() if self.input_mode == "stdin" else None
            return args, stdin_bytes, self._environment(ctx)
        if not self.template:
            raise RelayError(
                "AGENT_TEMPLATE_INVALID",
                f"{self.name} has no command_template configured. Re-register with 'relay add-agent'.",
            )
        model = ctx.model or ctx.config.get("default_model") or ""
        values = {
            "cli": executable,
            "request_file": str(ctx.request_file),
            "result_file": str(ctx.result_file),
            "artifact_dir": str(ctx.artifact_dir),
            "model": str(model),
        }
        args = render_command_template(self.template, values)

        extra_args = ctx.config.get("extra_args") or []
        if isinstance(extra_args, str):
            extra_args = shlex.split(extra_args)
        for item in extra_args:
            args.append(str(item))

        max_turns = ctx.config.get("max_turns")
        if max_turns not in (None, "", 0):
            args.extend(["--max-turns", str(int(max_turns))])

        env: dict[str, str] = {
            "RELAY_PROVIDER_NAME": self.name,
            "RELAY_JOB_ID": ctx.job_id,
            "RELAY_STAGING_RESULT": str(ctx.result_file),
            "RELAY_ARTIFACT_DIR": str(ctx.artifact_dir),
            "RELAY_RESULT_FORMAT": ctx.result_format,
        }
        env_extra = ctx.config.get("env_extra") or {}
        if isinstance(env_extra, dict):
            for key, value in env_extra.items():
                env[str(key)] = str(value)
        return args, None, env

    @staticmethod
    def _render_tokens(tokens: list[str], values: dict[str, str]) -> list[str]:
        return [
            _PLACEHOLDER_PATTERN.sub(lambda match: values.get(match.group(1), match.group(0)), token)
            for token in tokens
        ]

    @staticmethod
    def _manifest_values(ctx: AdapterContext, model: str) -> dict[str, str]:
        return {
            "request_file": str(ctx.request_file),
            "result_file": str(ctx.result_file),
            "artifact_dir": str(ctx.artifact_dir),
            "workspace": str(ctx.workspace),
            "schema_file": str(ctx.schema_file),
            "task": ctx.request_file.read_text(encoding="utf-8"),
            "model": str(model),
            "profile": str(ctx.profile),
            "job_id": str(ctx.job_id),
        }

    def discover_models(
        self,
        *,
        refresh: bool = False,
        include_hidden: bool = False,
        verify: bool = False,
    ) -> ModelCatalog:
        del refresh, verify
        if not self.model_list_argv:
            raise RelayError("MODEL_DISCOVERY_UNSUPPORTED", f"{self.name} has no model list command configured")
        if self.model_list_parser not in {"lines", "json"}:
            raise RelayError("AGENT_TEMPLATE_INVALID", f"Unsupported model list parser: {self.model_list_parser}")
        code, stdout, stderr = self.capture(self.model_list_argv, timeout=self.model_list_timeout_seconds)
        if code != 0:
            raise RelayError("MODEL_DISCOVERY_FAILED", stderr.strip() or f"{self.name} model list failed")
        try:
            values: list[Any]
            if self.model_list_parser == "lines":
                values = [line.strip() for line in stdout.splitlines() if line.strip()]
            else:
                parsed = json.loads(stdout)
                if isinstance(parsed, dict):
                    parsed = parsed.get("models", parsed.get("data", []))
                if not isinstance(parsed, list):
                    raise ValueError("JSON model list must be an array or contain models/data")
                values = parsed
        except (ValueError, json.JSONDecodeError) as exc:
            raise RelayError("MODEL_DISCOVERY_FAILED", f"{self.name} model list output is invalid: {exc}") from exc

        models: list[DiscoveredModel] = []
        seen: set[str] = set()
        for item in values:
            if isinstance(item, str):
                model_id = item.strip()
                display_name = model_id
                hidden = False
                metadata: dict[str, Any] = {}
            elif isinstance(item, dict):
                model_id = str(item.get("id") or item.get("slug") or item.get("name") or "").strip()
                display_name = str(item.get("display_name") or item.get("displayName") or model_id)
                hidden = bool(item.get("hidden", False))
                metadata = item
            else:
                continue
            if not model_id or model_id in seen or (hidden and not include_hidden):
                continue
            seen.add(model_id)
            models.append(
                DiscoveredModel(
                    id=model_id,
                    display_name=display_name,
                    selectable_name=model_id,
                    availability="available",
                    hidden=hidden,
                    metadata=metadata,
                )
            )
        return ModelCatalog(
            worker=self.name,
            cli_version=self.version(),
            status="ok",
            source="manifest_model_list",
            account_scoped=False,
            authoritative=False,
            models=models,
            warnings=["Models come from a user-defined command and are not independently verified by Relay."],
        )

    def subprocess_environment(self) -> dict[str, str] | None:
        if not self.manifest_mode:
            return None
        safety = self.worker_config.get("safety") or {}
        declared = safety.get("env_names") or [] if isinstance(safety, dict) else []
        names = _SAFE_ENV_NAMES | {str(name) for name in declared}
        return {name: os.environ[name] for name in names if name in os.environ}

    def _environment(self, ctx: AdapterContext) -> dict[str, str]:
        env: dict[str, str] = {
            "RELAY_PROVIDER_NAME": self.name,
            "RELAY_JOB_ID": ctx.job_id,
            "RELAY_STAGING_RESULT": str(ctx.result_file),
            "RELAY_ARTIFACT_DIR": str(ctx.artifact_dir),
            "RELAY_RESULT_FORMAT": ctx.result_format,
        }
        env_extra = self.worker_config.get("env_extra") or {}
        if isinstance(env_extra, dict):
            for key, value in env_extra.items():
                env[str(key)] = str(value)
        return env

    def normalize_output(self, ctx: AdapterContext, stdout_path: Path, stderr_path: Path) -> None:
        if ctx.result_file.exists() and ctx.result_file.stat().st_size:
            return
        raw = stdout_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            raise RelayError(
                "EMPTY_OUTPUT",
                f"{self.name} completed without a result file or stdout",
                True,
            )
        if ctx.result_format == "txt":
            ctx.result_file.write_text(raw, encoding="utf-8")
            return
        # JSON mode: expect the worker to have already written a compliant JSON.
        # If only stdout is present, copy it verbatim so the validator can reject it.
        ctx.result_file.write_text(raw, encoding="utf-8")
