from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import RelayError
from ..model_catalog import DiscoveredModel, ModelCatalog
from ..model_discovery import list_codex_models
from .base import Adapter, AdapterContext


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
        if self.full_access_mode_enabled():
            return "dangerously-bypass-approvals-and-sandbox"
        return str(self.worker_config.get("approval", "never"))

    def sandbox_mode(self) -> str:
        if self.full_access_mode_enabled():
            return "none"
        return str(self.worker_config.get("sandbox", "workspace-write"))

    def discover_models(
        self,
        *,
        refresh: bool = False,
        include_hidden: bool = False,
        verify: bool = False,
    ) -> ModelCatalog:
        exe = self.executable()
        if not exe:
            raise RelayError("WORKER_NOT_INSTALLED", "codex executable not found")

        try:
            result = list_codex_models(
                executable=exe,
                timeout_seconds=20.0,
                include_hidden=include_hidden,
            )
        except Exception as e:
            raise RelayError("MODEL_DISCOVERY_FAILED", f"Codex app-server model list failed: {e}") from e

        models = result if isinstance(result, list) else (result.get("data") or result.get("models", []))
        discovered = []
        for m in models:
            discovered.append(
                DiscoveredModel(
                    id=m.get("slug") or m.get("id") or "",
                    display_name=m.get("displayName") or m.get("display_name") or "",
                    selectable_name=m.get("slug") or m.get("id") or "",
                    availability="available",
                    is_default=m.get("isDefault") or m.get("is_default") or False,
                    hidden=m.get("hidden", False),
                    reasoning_efforts=[r.get("reasoningEffort") for r in m.get("supportedReasoningEfforts", [])]
                    if "supportedReasoningEfforts" in m
                    else m.get("supported_reasoning_levels", []),
                    default_reasoning_effort=m.get("defaultReasoningEffort") or m.get("default_reasoning_level"),
                    metadata=m,
                )
            )

        return ModelCatalog(
            worker=self.name,
            cli_version=self.version(),
            status="ok",
            source="app_server_model_list",
            account_scoped=True,
            authoritative=True,
            models=discovered,
        )

    def build_command(self, ctx: AdapterContext) -> tuple[list[str], bytes | None, dict[str, str]]:
        exe = self.executable()
        if not exe:
            raise RelayError("WORKER_NOT_INSTALLED", "Codex CLI executable was not found")
        args = [
            exe,
            "exec",
            "--ephemeral",
        ]

        if self.full_access_mode_enabled():
            args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            args.extend(["--sandbox", str(ctx.config.get("sandbox", "workspace-write"))])
        args.extend(
            [
                "--skip-git-repo-check",
                "--color",
                "never",
                "-C",
                str(ctx.workspace),
                "--output-last-message",
                str(ctx.result_file),
            ]
        )
        model = ctx.model or ctx.config.get("default_model")
        if model:
            args.extend(["--model", str(model)])
        if ctx.result_format == "json":
            args.extend(["--output-schema", str(ctx.schema_file)])
        args.append("-")
        prompt = (
            b"Read request.md in the current working directory and complete it without asking questions. "
            b"Return only the requested final JSON or text. "
            b"Follow request.md for target/ edits. Do not attempt direct filesystem writes for artifacts "
            b"other than those target/ edits. Put every other JSON artifact's exact "
            b"content in the structured artifacts payload required by schema.json; Relay will safely materialize "
            b"the files, and the valid artifact payload counts as completed work. Artifact relative_path values "
            b"are relative to ./artifacts and must not start with artifacts/."
        )
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

        if stderr_path.exists():
            stderr_raw = stderr_path.read_text(encoding="utf-8", errors="replace")
            if self.has_permission_error(stderr_raw) and not self.full_access_mode_enabled():
                raise RelayError(
                    "PERMISSION_BLOCKED",
                    self.permission_failure_message("Codex reported an access or sandbox permission error"),
                    False,
                )

        if not raw:
            raise RelayError("OUTPUT_NOT_CREATED", "Codex did not create its output-last-message file", True)
        ctx.result_file.write_text(raw, encoding="utf-8")
