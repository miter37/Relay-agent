from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Adapter, AdapterContext
from ..errors import RelayError


class CodexAdapter(Adapter):
    name = "codex"
    command_name = "codex"

    def detect_capabilities(self, help_text: str) -> dict[str, Any]:
        return {
            "exec_hint": "exec" in help_text,
            "sandbox_hint": "--sandbox" in help_text,
            "approval_hint": "--ask-for-approval" in help_text,
            "output_file_hint": "--output-last-message" in help_text or "-o" in help_text,
            "schema_hint": "--output-schema" in help_text,
        }

    def permission_mode(self) -> str:
        return str(self.worker_config.get("approval", "never"))

    def sandbox_mode(self) -> str:
        return str(self.worker_config.get("sandbox", "workspace-write"))

    def build_command(self, ctx: AdapterContext) -> tuple[list[str], bytes | None, dict[str, str]]:
        exe = self.executable()
        if not exe:
            raise RelayError("WORKER_NOT_INSTALLED", "Codex CLI executable was not found")
        args = [
            exe,
            "exec",
            "--ephemeral",
            "--sandbox",
            str(ctx.config.get("sandbox", "workspace-write")),
        ]
        if ctx.config.get("dangerously_skip_permissions", True):
            args.append("--dangerously-bypass-approvals-and-sandbox")

        args.extend([
            "--skip-git-repo-check",
            "--color",
            "never",
            "-C",
            str(ctx.workspace),
            "--output-last-message",
            str(ctx.result_file),
        ])
        model = ctx.model or ctx.config.get("default_model")
        if model:
            args.extend(["--model", str(model)])
        if ctx.result_format == "json":
            args.extend(["--output-schema", str(ctx.schema_file)])
        args.append("-")
        prompt = (
            "Read request.md in the current working directory and complete it without asking questions. "
            "Return only the requested final JSON or text. "
            "Any artifact files must be created only in ./artifacts and must contain the exact requested content."
        ).encode("utf-8")
        env = {
            "RELAY_PROVIDER_NAME": "codex",
            "RELAY_JOB_ID": ctx.job_id,
            "RELAY_STAGING_RESULT": str(ctx.result_file),
            "RELAY_ARTIFACT_DIR": str(ctx.artifact_dir),
            "RELAY_RESULT_FORMAT": ctx.result_format,
        }
        return args, prompt, env

    def normalize_output(self, ctx: AdapterContext, stdout_path: Path, stderr_path: Path) -> None:
        if ctx.result_file.exists() and ctx.result_file.stat().st_size:
            if ctx.result_format == "json":
                raw = ctx.result_file.read_text(encoding="utf-8", errors="strict")
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RelayError("INVALID_JSON", "Codex output-last-message was not valid JSON", True) from exc
                ctx.result_file.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        raw = stdout_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            raise RelayError("OUTPUT_NOT_CREATED", "Codex did not create its output-last-message file", True)
        ctx.result_file.write_text(raw, encoding="utf-8")
