from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Config
from ..db import Database
from ..errors import RelayError
from ..target_workspace import safe_resolve
from ..util import canonical_json

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
            files_to_write.append((f"routines/{r['routine_id']}.json", canonical_json(r).encode("utf-8")))

        # 4. Runs & Artifacts (if requested)
        if include_runs:
            jobs = self.db.list_jobs_page(bucket="all", limit=10000)
            for j in sorted(jobs, key=lambda x: x["job_id"]):
                files_to_write.append((f"runs/task_run_{j['job_id']}.json", canonical_json(j).encode("utf-8")))
                artifacts = self.db.artifacts_for_job(j["job_id"])
                for a in artifacts:
                    uid = a.get("artifact_uid") or str(a["artifact_id"])
                    files_to_write.append((f"artifacts/{uid}.meta.json", canonical_json(a).encode("utf-8")))
                    fp = safe_resolve(Path(str(a["final_path"])))
                    if fp.is_file():
                        files_to_write.append((f"artifacts/{uid}.bin", fp.read_bytes()))

        # Sort all entries alphabetically for deterministic zip output
        files_to_write.sort(key=lambda item: item[0])

        # Manifest
        manifest = {
            "export_schema_version": EXPORT_SCHEMA_VERSION,
            "relay_version": __version__,
            "include_runs": include_runs,
            "entry_count": len(files_to_write),
            "files": [item[0] for item in files_to_write],
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
