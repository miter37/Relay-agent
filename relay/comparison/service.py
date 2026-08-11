from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..errors import RelayError
from ..target_workspace import safe_resolve

MAX_DIFF_BYTES = 262144  # 256 KiB cap to bound context


class ComparisonService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def compare_runs(self, a_run_id: str, b_run_id: str) -> dict[str, Any]:
        job_a = self.db.get_job(a_run_id)
        job_b = self.db.get_job(b_run_id)

        prun_a = self.db.get_project_run(a_run_id) if not job_a else None
        prun_b = self.db.get_project_run(b_run_id) if not job_b else None

        if not (job_a or prun_a):
            raise RelayError("JOB_NOT_FOUND", f"Run not found: {a_run_id}")
        if not (job_b or prun_b):
            raise RelayError("JOB_NOT_FOUND", f"Run not found: {b_run_id}")

        if bool(job_a) != bool(job_b):
            raise RelayError("COMPARE_INCOMPATIBLE", "Cannot compare a Task Run with a Project Run.")

        if job_a and job_b:
            return self._compare_task_runs(job_a, job_b)
        else:
            return self._compare_project_runs(prun_a, prun_b)

    def _compare_task_runs(self, job_a: dict[str, Any], job_b: dict[str, Any]) -> dict[str, Any]:
        a_id = job_a["job_id"]
        b_id = job_b["job_id"]

        meta_keys = [
            "caller",
            "submitted_via",
            "trigger_type",
            "requested_worker",
            "actual_worker",
            "format",
            "profile",
            "status",
            "result_status",
        ]
        metadata_diff = {}
        for key in meta_keys:
            val_a = job_a.get(key)
            val_b = job_b.get(key)
            if val_a != val_b:
                metadata_diff[key] = {"a": val_a, "b": val_b}

        arts_a = {a.get("role", "output") + ":" + a["relative_path"]: a for a in self.db.artifacts_for_job(a_id)}
        arts_b = {b.get("role", "output") + ":" + b["relative_path"]: b for b in self.db.artifacts_for_job(b_id)}

        only_in_a = [a for k, a in arts_a.items() if k not in arts_b]
        only_in_b = [b for k, b in arts_b.items() if k not in arts_a]
        shared_different_hash = []
        identical = []

        for k, a in arts_a.items():
            if k in arts_b:
                b = arts_b[k]
                if a["sha256"] != b["sha256"]:
                    shared_different_hash.append(
                        {"key": k, "a_uid": a.get("artifact_uid"), "b_uid": b.get("artifact_uid")}
                    )
                else:
                    identical.append(k)

        lineage_a = {item["alias"]: item for item in self.db.lineage_for_job(a_id)}
        lineage_b = {item["alias"]: item for item in self.db.lineage_for_job(b_id)}
        lineage_diff = {}
        all_aliases = set(lineage_a.keys()) | set(lineage_b.keys())
        for alias in sorted(all_aliases):
            la = lineage_a.get(alias)
            lb = lineage_b.get(alias)
            if la != lb:
                lineage_diff[alias] = {
                    "a_source_job_id": la["source_job_id"] if la else None,
                    "b_source_job_id": lb["source_job_id"] if lb else None,
                    "a_sha256": la["snapshot_sha256"] if la else None,
                    "b_sha256": lb["snapshot_sha256"] if lb else None,
                }

        return {
            "kind": "task_run_comparison",
            "a_run_id": a_id,
            "b_run_id": b_id,
            "metadata_diff": metadata_diff,
            "artifacts_diff": {
                "only_in_a": [a.get("artifact_uid") or a["relative_path"] for a in only_in_a],
                "only_in_b": [b.get("artifact_uid") or b["relative_path"] for b in only_in_b],
                "shared_different_hash": shared_different_hash,
                "identical_count": len(identical),
            },
            "lineage_diff": lineage_diff,
        }

    def _compare_project_runs(self, prun_a: dict[str, Any], prun_b: dict[str, Any]) -> dict[str, Any]:
        a_id = prun_a["project_run_id"]
        b_id = prun_b["project_run_id"]

        meta_keys = ["project_id", "project_version", "status", "trigger_type", "submitted_via"]
        metadata_diff = {}
        for key in meta_keys:
            val_a = prun_a.get(key)
            val_b = prun_b.get(key)
            if val_a != val_b:
                metadata_diff[key] = {"a": val_a, "b": val_b}

        steps_a = {s["node_id"]: s for s in self.db.list_project_steps(a_id)}
        steps_b = {s["node_id"]: s for s in self.db.list_project_steps(b_id)}

        step_diffs = {}
        all_nodes = set(steps_a.keys()) | set(steps_b.keys())
        for node in sorted(all_nodes):
            sa = steps_a.get(node)
            sb = steps_b.get(node)
            if sa != sb:
                step_diffs[node] = {
                    "a_status": sa["status"] if sa else None,
                    "b_status": sb["status"] if sb else None,
                    "a_task_run_id": sa.get("active_task_run_id") if sa else None,
                    "b_task_run_id": sb.get("active_task_run_id") if sb else None,
                }

        return {
            "kind": "project_run_comparison",
            "a_run_id": a_id,
            "b_run_id": b_id,
            "metadata_diff": metadata_diff,
            "step_diffs": step_diffs,
        }

    def diff_artifacts(self, a_uid: str, b_uid: str, max_bytes: int = MAX_DIFF_BYTES) -> dict[str, Any]:
        art_a = self.db.artifact_by_uid(a_uid)
        art_b = self.db.artifact_by_uid(b_uid)

        if not art_a:
            raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {a_uid}")
        if not art_b:
            raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {b_uid}")

        file_a = safe_resolve(Path(str(art_a["final_path"])))
        file_b = safe_resolve(Path(str(art_b["final_path"])))

        metadata_diff = {
            "role": {"a": art_a.get("role"), "b": art_b.get("role")},
            "mime_type": {"a": art_a.get("mime_type"), "b": art_b.get("mime_type")},
            "size": {"a": art_a.get("size"), "b": art_b.get("size")},
            "sha256": {"a": art_a.get("sha256"), "b": art_b.get("sha256")},
        }

        if not file_a.is_file() or not file_b.is_file():
            return {
                "a_artifact_uid": a_uid,
                "b_artifact_uid": b_uid,
                "metadata_diff": metadata_diff,
                "diff_available": False,
                "reason": "One or both artifact files are missing on disk.",
            }

        if file_a.stat().st_size > max_bytes or file_b.stat().st_size > max_bytes:
            raise RelayError("DIFF_TOO_LARGE", f"Artifact size exceeds maximum diff limit of {max_bytes} bytes.")

        try:
            content_a = file_a.read_text(encoding="utf-8")
            content_b = file_b.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "a_artifact_uid": a_uid,
                "b_artifact_uid": b_uid,
                "metadata_diff": metadata_diff,
                "diff_available": False,
                "reason": "Binary or non-UTF-8 artifact content.",
            }

        lines_a = content_a.splitlines(keepends=True)
        lines_b = content_b.splitlines(keepends=True)
        text_diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=a_uid, tofile=b_uid))

        json_diff = None
        if art_a.get("mime_type") == "application/json" or file_a.suffix == ".json":
            try:
                json_a = json.loads(content_a)
                json_b = json.loads(content_b)
                if isinstance(json_a, dict) and isinstance(json_b, dict):
                    json_diff = self._diff_json_dicts(json_a, json_b)
            except Exception:
                pass

        return {
            "a_artifact_uid": a_uid,
            "b_artifact_uid": b_uid,
            "metadata_diff": metadata_diff,
            "diff_available": True,
            "text_diff": text_diff,
            "json_diff": json_diff,
        }

    @staticmethod
    def _diff_json_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        all_keys = set(a.keys()) | set(b.keys())
        only_in_a = [k for k in sorted(all_keys) if k in a and k not in b]
        only_in_b = [k for k in sorted(all_keys) if k in b and k not in a]
        changed = {k: {"a": a[k], "b": b[k]} for k in sorted(all_keys) if k in a and k in b and a[k] != b[k]}
        return {"only_in_a": only_in_a, "only_in_b": only_in_b, "changed": changed}
