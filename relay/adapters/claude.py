from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Adapter, AdapterContext
from ..errors import RelayError


class ClaudeAdapter(Adapter):
    name = "claude"
    command_name = "claude"

    def detect_capabilities(self, help_text: str) -> dict[str, Any]:
        # Claude documentation explicitly warns that --help may omit valid flags.
        return {
            "print_mode_hint": "-p" in help_text or "--print" in help_text,
            "output_json_hint": "--output-format" in help_text,
            "permission_mode_hint": "--permission-mode" in help_text,
            "json_schema_hint": "--json-schema" in help_text,
            "warning": "Help output is advisory only; deep probe is authoritative.",
        }

    def permission_mode(self) -> str:
        return "bypassPermissions"

    def build_command(self, ctx: AdapterContext) -> tuple[list[str], bytes | None, dict[str, str]]:
        exe = self.executable()
        if not exe:
            raise RelayError("WORKER_NOT_INSTALLED", "Claude Code executable was not found")
        prompt = (
            "Read the UTF-8 file request.md in the current working directory. "
            "Complete the task non-interactively. Return only the final result matching the requested format. "
            "Create any requested artifacts only in the artifacts directory."
        )
        args = [
            exe,
            "-p",
            prompt,
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "json" if ctx.result_format == "json" else "text",
            "--no-session-persistence",
            "--max-turns",
            str(int(ctx.config.get("max_turns", 30))),
        ]
        model = ctx.model or ctx.config.get("default_model")
        if model:
            args.extend(["--model", str(model)])
        if ctx.config.get("max_budget_usd") not in (None, ""):
            args.extend(["--max-budget-usd", str(ctx.config["max_budget_usd"])])
        tools = str(ctx.config.get("tools", "")).strip()
        if tools:
            args.extend(["--tools", tools])
        disallowed = str(ctx.config.get("disallowed_tools", "")).strip()
        if disallowed:
            args.extend(["--disallowedTools", disallowed])
        if ctx.result_format == "json":
            schema = ctx.schema_file.read_text(encoding="utf-8")
            args.extend(["--json-schema", json.dumps(json.loads(schema), separators=(",", ":"))])
        env = {
            "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
            "RELAY_PROVIDER_NAME": "claude",
            "RELAY_JOB_ID": ctx.job_id,
            "RELAY_STAGING_RESULT": str(ctx.result_file),
            "RELAY_ARTIFACT_DIR": str(ctx.artifact_dir),
            "RELAY_RESULT_FORMAT": ctx.result_format,
        }
        return args, None, env

    def normalize_output(self, ctx: AdapterContext, stdout_path: Path, stderr_path: Path) -> None:
        if ctx.result_file.exists() and ctx.result_file.stat().st_size:
            return
        raw = stdout_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            raise RelayError("EMPTY_OUTPUT", "Claude completed without a result file or stdout", True)
        if ctx.result_format == "txt":
            ctx.result_file.write_text(raw, encoding="utf-8")
            return
        try:
            wrapper = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RelayError("INVALID_JSON", "Claude stdout was not valid JSON", True) from exc
        candidate: Any = wrapper
        if isinstance(wrapper, dict):
            if isinstance(wrapper.get("structured_output"), dict):
                candidate = wrapper["structured_output"]
            elif "result" in wrapper:
                candidate = wrapper["result"]
        if isinstance(candidate, str):
            candidate = candidate.strip()
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                candidate = candidate[start:end + 1]
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise RelayError("INVALID_JSON", "Claude result field did not contain JSON", True) from exc
        if not isinstance(candidate, dict):
            raise RelayError("INVALID_JSON", "Claude structured output was not an object", True)
        ctx.result_file.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
