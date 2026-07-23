from __future__ import annotations

import shutil
from typing import Any

from .adapters import get_adapter
from .adapters.base import AdapterContext
from .config import Config
from .db import Database
from .errors import RelayError
from .models import AdapterSpec
from .process_supervisor import run_supervised
from .request_builder import write_schema
from .util import ensure_dir, new_job_id, utc_now
from .validation import materialize_artifact_payloads, scan_artifacts, validate_json_result


class Doctor:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.spec_root = config.path_value("adapter_spec_root")

    def audit(self, workers: list[str], deep: bool = False) -> dict[str, Any]:
        results = []
        for worker in workers:
            cfg = self.config.worker(worker)
            adapter = get_adapter(worker, cfg, self.spec_root)
            spec = adapter.shallow_audit()
            self.db.add_audit(
                worker, spec.version, "shallow", "passed" if spec.shallow_ok else "failed", spec.to_dict()
            )
            if deep and spec.shallow_ok:
                spec = self._deep_probe(adapter, spec)
            results.append(spec.to_dict())
        healthy = sum(1 for item in results if item["status"] == "healthy")
        return {"ok": healthy == len(results), "deep": deep, "workers": results}

    def _deep_probe(self, adapter, spec: AdapterSpec) -> AdapterSpec:
        worker = adapter.name
        probe_id = "doctor-" + new_job_id()
        workspace = self.config.path_value("workspace_root") / worker / probe_id
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
        ensure_dir(workspace / "output")
        artifact_dir = ensure_dir(workspace / "artifacts")
        runtime = ensure_dir(workspace / "runtime")
        result_file = workspace / "output" / "result.json.partial"
        schema_file = workspace / "schema.json"
        write_schema(schema_file)
        request_file = workspace / "request.md"
        request_file.write_text(
            """# Relay Deep Doctor Probe

Create a JSON result matching schema.json with:
- schema_version: \"1.0\"
- status: \"complete\"
- answer: \"RELAY_UNATTENDED_OK\"
- sources: []
- uncertainties: []
- missing_items: []
- artifacts: one item for probe-artifact.txt

Create artifacts/probe-artifact.txt containing exactly RELAY_ARTIFACT_OK.
Do not ask questions. Do not wait for user input.
""",
            encoding="utf-8",
        )
        ctx = AdapterContext(
            job_id=probe_id,
            workspace=workspace,
            request_file=request_file,
            result_file=result_file,
            artifact_dir=artifact_dir,
            schema_file=schema_file,
            result_format="json",
            profile="doctor",
            model=None,
            config=adapter.worker_config,
        )
        details = dict(spec.details)
        try:
            command, stdin_bytes, env_extra = adapter.build_command(ctx)
            outcome = run_supervised(
                command=command,
                cwd=workspace,
                stdin_bytes=stdin_bytes,
                env_extra=env_extra,
                stdout_path=runtime / "stdout.log",
                stderr_path=runtime / "stderr.log",
                timeout_seconds=min(int(self.config.get("timeout_seconds", 1200)), 300),
                soft_stall_seconds=min(int(self.config.get("soft_stall_seconds", 120)), 60),
                hard_stall_seconds=min(int(self.config.get("hard_stall_seconds", 300)), 120),
                poll_seconds=0.5,
            )
            if outcome.failure_code:
                raise RelayError(outcome.failure_code, f"Probe ended with {outcome.failure_code}")
            if outcome.exit_code != 0:
                stderr = outcome.stderr_path.read_text(encoding="utf-8", errors="replace")
                code, _ = adapter.classify_failure(outcome.exit_code, stderr)
                raise RelayError(code, f"Probe exited with code {outcome.exit_code}")
            adapter.normalize_output(ctx, outcome.stdout_path, outcome.stderr_path)
            value = validate_json_result(result_file, 5 * 1024 * 1024)
            materialize_artifact_payloads(value, artifact_dir, 10, 10 * 1024 * 1024)
            artifacts = scan_artifacts(artifact_dir, 10, 10 * 1024 * 1024)
            artifact_ok = any(
                item["relative_path"] == "probe-artifact.txt"
                and (artifact_dir / item["relative_path"]).read_text(encoding="utf-8").strip() == "RELAY_ARTIFACT_OK"
                for item in artifacts
            )
            output_ok = value.get("answer") == "RELAY_UNATTENDED_OK"
            unattended_ok = not outcome.interactive_prompt_detected and not outcome.stalled
            deep_ok = output_ok and artifact_ok and unattended_ok
            details.update(
                {
                    "probe_exit_code": outcome.exit_code,
                    "probe_duration_seconds": round(outcome.duration_seconds, 2),
                    "probe_result": value,
                    "probe_artifacts": artifacts,
                }
            )
            spec.deep_ok = deep_ok
            spec.unattended_ok = unattended_ok
            spec.output_ok = output_ok
            spec.artifact_ok = artifact_ok
            spec.status = "healthy" if deep_ok else "unhealthy"
            spec.audited_at = utc_now()
            spec.details = details
            adapter.save_spec(spec)
            self.db.add_audit(
                worker,
                spec.version,
                "deep",
                "passed" if deep_ok else "failed",
                details,
                adapter.spec_hash(spec),
            )
            return spec
        except RelayError as err:
            details.update({"probe_error_code": err.code, "probe_error": err.message})
            spec.deep_ok = False
            spec.unattended_ok = False
            spec.output_ok = False
            spec.artifact_ok = False
            spec.status = "unhealthy"
            spec.audited_at = utc_now()
            spec.details = details
            adapter.save_spec(spec)
            self.db.add_audit(worker, spec.version, "deep", "failed", details, adapter.spec_hash(spec))
            return spec
