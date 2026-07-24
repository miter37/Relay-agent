from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import RelayError
from ..model_catalog import DiscoveredModel, ModelCatalog
from ..model_discovery import parse_agy_models
from .base import Adapter, AdapterContext


class AntigravityAdapter(Adapter):
    name = "antigravity"
    command_name = "agy"

    def detect_capabilities(self, help_text: str) -> dict[str, Any]:
        return {
            "print_mode_hint": "-p" in help_text or "non-interactive" in help_text.lower(),
            "skip_permissions_hint": "--dangerously-skip-permissions" in help_text,
            "model_hint": "--model" in help_text,
            "warning": "Antigravity remains opt-in until a deep probe passes on the installed version.",
        }

    def permission_mode(self) -> str:
        return "dangerously-skip-permissions" if self.full_access_mode_enabled() else "default"

    def discover_models(
        self,
        *,
        refresh: bool = False,
        include_hidden: bool = False,
        verify: bool = False,
    ) -> ModelCatalog:
        code, stdout, stderr = self.capture(["models"], timeout=30)
        if code != 0:
            raise RelayError(
                "MODEL_DISCOVERY_FAILED",
                stderr.strip() or "agy models failed",
            )
        model_names = parse_agy_models(stdout)
        discovered = [
            DiscoveredModel(id=m, display_name=m, selectable_name=m, availability="available") for m in model_names
        ]
        return ModelCatalog(
            worker=self.name,
            cli_version=self.version(),
            status="ok",
            source="agy_models",
            account_scoped=True,
            authoritative=True,
            models=discovered,
        )

    def build_command(self, ctx: AdapterContext) -> tuple[list[str], bytes | None, dict[str, str]]:
        exe = self.executable()
        if not exe:
            raise RelayError("WORKER_NOT_INSTALLED", "Antigravity CLI executable was not found")
        prompt = (
            "Read request.md in the current directory and complete the task non-interactively. "
            f"Write the final {ctx.result_format.upper()} result to {ctx.result_file.relative_to(ctx.workspace).as_posix()}. "
            "Follow request.md for target/ edits; write other artifact files only in the artifacts directory. "
            "Do not ask questions."
        )
        args = [exe]
        if self.full_access_mode_enabled():
            args.append("--dangerously-skip-permissions")
        model = ctx.model or ctx.config.get("default_model")
        if model:
            args.extend(["--model", str(model)])
        args.extend(["-p", prompt])
        env = {
            "RELAY_PROVIDER_NAME": "antigravity",
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

        if stderr_path.exists():
            stderr_raw = stderr_path.read_text(encoding="utf-8", errors="replace")
            if self.has_permission_error(stderr_raw) and not self.full_access_mode_enabled():
                raise RelayError(
                    "PERMISSION_BLOCKED",
                    self.permission_failure_message("Antigravity reported an access or sandbox permission error"),
                    False,
                )

        if not raw:
            raise RelayError("EMPTY_OUTPUT", "Antigravity returned no result file and no stdout", True)
        if ctx.result_format == "txt":
            ctx.result_file.write_text(raw, encoding="utf-8")
            return
        # Antigravity currently has no stable structured-output contract in Relay; require raw JSON.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RelayError("INVALID_JSON", "Antigravity stdout did not contain valid JSON", True) from exc
        ctx.result_file.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
