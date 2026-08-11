from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from .. import __version__
from ..config import Config
from ..db import Database
from ..errors import RelayError
from ..target_workspace import safe_resolve
from ..util import canonical_json, is_within

EXPORT_SCHEMA_VERSION = 1
FIXED_ZIP_DATE = (2026, 8, 4, 0, 0, 0)


class ExportService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def export(self, *, include_runs: bool = False, out_path: Path | str | None = None) -> Path:
        if out_path is None:
            out_path = self.config.path_value("runtime_root") / "relay-export.zip"
        dest = safe_resolve(Path(out_path))
        dest.parent.mkdir(parents=True, exist_ok=True)

        files_to_write: list[tuple[str, bytes]] = []

        # 1. Tasks
        tasks = self.db.list_tasks(limit=10000)
        for t in sorted(tasks, key=lambda x: x["task_id"]):
            files_to_write.append((f"tasks/{t['task_id']}.json", canonical_json(t).encode("utf-8")))

        # 2. Projects
        projects = self.db.list_projects(include_deleted=True, limit=10000)
        for p in sorted(projects, key=lambda x: x["project_id"]):
            files_to_write.append((f"projects/{p['project_id']}.json", canonical_json(p).encode("utf-8")))

        # 3. Routines
        routines = self.db.list_routines(include_deleted=True, limit=10000)
        for r in sorted(routines, key=lambda x: x["routine_id"]):
            safe_routine = dict(r)
            if safe_routine.get("notification_policy_json"):
                policy = json.loads(safe_routine["notification_policy_json"])
                safe_routine["notification_policy_json"] = canonical_json(_without_secrets(policy))
            files_to_write.append((f"routines/{r['routine_id']}.json", canonical_json(safe_routine).encode("utf-8")))

        # 4. Runs & Artifacts (if requested)
        if include_runs:
            jobs = self.db.list_jobs(limit=10000)
            lineage: list[dict] = []
            for j in sorted(jobs, key=lambda x: x["job_id"]):
                files_to_write.append((f"runs/task_run_{j['job_id']}.json", canonical_json(j).encode("utf-8")))
                lineage.extend(self.db.lineage_for_job(j["job_id"]))
                artifacts = self.db.artifacts_for_job(j["job_id"])
                for a in artifacts:
                    uid = a.get("artifact_uid") or str(a["artifact_id"])
                    safe_artifact = {key: value for key, value in a.items() if key != "final_path"}
                    files_to_write.append((f"artifacts/{uid}.meta.json", canonical_json(safe_artifact).encode("utf-8")))
                    fp = safe_resolve(Path(str(a["final_path"])))
                    artifact_root = self.config.path_value("artifact_root")
                    if not is_within(fp, artifact_root):
                        raise RelayError("EXPORT_FAILED", f"Artifact is outside Relay artifact storage: {fp}")
                    if not fp.is_file():
                        raise RelayError("EXPORT_FAILED", f"Artifact file is missing: {fp}")
                    data = fp.read_bytes()
                    if len(data) != int(a["size"]) or hashlib.sha256(data).hexdigest() != a["sha256"]:
                        raise RelayError("EXPORT_FAILED", f"Artifact content changed: {uid}")
                    files_to_write.append((f"artifacts/{uid}.bin", data))
            files_to_write.append(("lineage.json", canonical_json(lineage).encode("utf-8")))

        # Sort all entries alphabetically for deterministic zip output
        files_to_write.sort(key=lambda item: item[0])

        # Manifest
        manifest = {
            "export_schema_version": EXPORT_SCHEMA_VERSION,
            "relay_version": __version__,
            "include_runs": include_runs,
            "entry_count": len(files_to_write),
            "files": [item[0] for item in files_to_write],
            "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files_to_write},
        }
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        files_to_write.insert(0, ("manifest.json", manifest_bytes))

        # Write zip with fixed ZipInfo timestamps for determinism
        with zipfile.ZipFile(dest, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arcname, data in files_to_write:
                zinfo = zipfile.ZipInfo(filename=arcname, date_time=FIXED_ZIP_DATE)
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zinfo, data)

        return dest


def _without_secrets(value):
    if isinstance(value, dict):
        return {key: _without_secrets(item) for key, item in value.items() if key.lower() != "secret"}
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value
