from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from ..errors import RelayError
from .base import Adapter, AdapterContext

BUILTIN_WORKERS = frozenset({"claude", "codex", "antigravity"})

KNOWN_PLACEHOLDERS = frozenset({"cli", "request_file", "result_file", "artifact_dir", "model"})

_PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_WORKER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


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
        self.command_name = str(worker_config.get("command") or self.command_name or self.name)
        self.template = str(worker_config.get("command_template", ""))

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
