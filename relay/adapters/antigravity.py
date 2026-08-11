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
        # agy 1.1.x may resolve relative paths under ~/.gemini/antigravity-cli/scratch
        # instead of the process cwd. Always pass absolute paths and --add-dir.
        result_abs = ctx.result_file.resolve()
        artifact_abs = ctx.artifact_dir.resolve()
        workspace_abs = ctx.workspace.resolve()
        request_abs = ctx.request_file.resolve()
        prompt = (
            f"Read the task file at {request_abs.as_posix()} and complete it non-interactively. "
            f"Write the final {ctx.result_format.upper()} result ONLY to this absolute path: {result_abs.as_posix()}. "
            f"Write artifact files ONLY under this absolute directory: {artifact_abs.as_posix()}. "
            f"The Relay workspace root is {workspace_abs.as_posix()}. "
            "Do not write results only under ~/.gemini scratch. Do not ask questions."
        )
        args = [exe]
        if self.full_access_mode_enabled():
            args.append("--dangerously-skip-permissions")
        args.extend(["--add-dir", str(workspace_abs)])
        model = ctx.model or ctx.config.get("default_model")
        if model:
            args.extend(["--model", str(model)])
        args.extend(["-p", prompt])
        env = {
            "RELAY_PROVIDER_NAME": "antigravity",
            "RELAY_JOB_ID": ctx.job_id,
            "RELAY_STAGING_RESULT": str(result_abs),
            "RELAY_ARTIFACT_DIR": str(artifact_abs),
            "RELAY_RESULT_FORMAT": ctx.result_format,
        }
        return args, None, env

    def normalize_output(self, ctx: AdapterContext, stdout_path: Path, stderr_path: Path) -> None:
        if ctx.result_file.exists() and ctx.result_file.stat().st_size:
            return
        # agy sometimes writes under alternate names or its Gemini scratch tree.
        scratch_root = Path.home() / ".gemini" / "antigravity-cli" / "scratch"
        candidates = [
            ctx.workspace / "output" / "result.json",
            ctx.workspace / "result.json",
            scratch_root / "output" / "result.json.partial",
            scratch_root / "output" / "result.json",
            scratch_root / "result.json",
        ]
        if ctx.result_file.name.endswith(".partial"):
            candidates.insert(0, ctx.result_file.with_name(ctx.result_file.name[: -len(".partial")]))
        for candidate in candidates:
            try:
                if not candidate.exists() or not candidate.stat().st_size:
                    continue
                if candidate.resolve() == ctx.result_file.resolve():
                    return
                ctx.result_file.parent.mkdir(parents=True, exist_ok=True)
                ctx.result_file.write_text(candidate.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                if ctx.result_file.stat().st_size:
                    # Best-effort: also copy probe artifact if only present in scratch.
                    scratch_art = scratch_root / "artifacts" / "probe-artifact.txt"
                    target_art = ctx.artifact_dir / "probe-artifact.txt"
                    if scratch_art.exists() and not target_art.exists():
                        target_art.parent.mkdir(parents=True, exist_ok=True)
                        target_art.write_text(
                            scratch_art.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
                        )
                    return
            except OSError:
                pass

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
        # Strip common markdown fences before locating the JSON object.
        fenced = raw
        if "```" in fenced:
            parts = fenced.split("```")
            for part in parts:
                chunk = part.strip()
                if chunk.lower().startswith("json"):
                    chunk = chunk[4:].lstrip()
                if chunk.startswith("{"):
                    fenced = chunk
                    break
        start = fenced.find("{")
        end = fenced.rfind("}")
        if start >= 0 and end > start:
            fenced = fenced[start : end + 1]
        try:
            value = json.loads(fenced)
        except json.JSONDecodeError as exc:
            raise RelayError("INVALID_JSON", "Antigravity stdout did not contain valid JSON", True) from exc
        ctx.result_file.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
