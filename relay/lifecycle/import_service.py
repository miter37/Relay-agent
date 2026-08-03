from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..errors import RelayError
from ..target_workspace import safe_resolve


class ImportService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def import_archive(
        self,
        archive_path: Path | str,
        *,
        conflict: str = "skip",
        include_runs: bool = False,
    ) -> dict[str, Any]:
        if conflict not in {"skip", "overwrite", "rename"}:
            raise RelayError("INVALID_REQUEST", f"Invalid conflict policy: {conflict}")

        path = safe_resolve(Path(archive_path))
        if not path.is_file():
            raise RelayError("IMPORT_ARCHIVE_INVALID", f"Archive file not found: {archive_path}")

        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                if "manifest.json" not in names:
                    raise RelayError("IMPORT_ARCHIVE_INVALID", "Archive missing manifest.json")

                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                if manifest.get("export_schema_version") != 1:
                    raise RelayError("IMPORT_ARCHIVE_INVALID", "Unsupported export schema version")

                imported_tasks = 0
                imported_projects = 0
                imported_routines = 0
                conflicts = 0

                # 1. Tasks
                task_files = [n for n in names if n.startswith("tasks/") and n.endswith(".json")]
                existing_tasks_by_name = {t["name"]: t for t in self.db.list_tasks(limit=10000)}

                for tf in task_files:
                    t = json.loads(zf.read(tf).decode("utf-8"))
                    name = t.get("name") or "Imported Task"
                    if name in existing_tasks_by_name:
                        conflicts += 1
                        if conflict == "skip":
                            continue
                        elif conflict == "rename":
                            t["name"] = f"Imported {name}"
                            t["task_id"] = __import__("relay.util", fromlist=["new_job_id"]).new_job_id()
                            self.db.create_task(t)
                            imported_tasks += 1
                        elif conflict == "overwrite":
                            exist_id = existing_tasks_by_name[name]["task_id"]
                            self.db.update_task(exist_id, **{k: v for k, v in t.items() if k not in {"task_id", "version", "created_at", "updated_at"}})
                            imported_tasks += 1
                    else:
                        # Direct import
                        self.db.create_task(t)
                        imported_tasks += 1

                # 2. Projects
                project_files = [n for n in names if n.startswith("projects/") and n.endswith(".json")]
                existing_projects_by_name = {p["name"]: p for p in self.db.list_projects(include_deleted=True, limit=10000)}

                for pf in project_files:
                    p = json.loads(zf.read(pf).decode("utf-8"))
                    name = p.get("name") or "Imported Project"
                    if name in existing_projects_by_name:
                        conflicts += 1
                        if conflict == "skip":
                            continue
                        elif conflict == "rename":
                            p["name"] = f"Imported {name}"
                            p["project_id"] = __import__("relay.util", fromlist=["new_job_id"]).new_job_id()
                            self.db.create_project(p)
                            imported_projects += 1
                        elif conflict == "overwrite":
                            exist_id = existing_projects_by_name[name]["project_id"]
                            self.db.update_project(exist_id, **{k: v for k, v in p.items() if k not in {"project_id", "version", "created_at", "updated_at"}})
                            imported_projects += 1
                    else:
                        self.db.create_project(p)
                        imported_projects += 1

                # 3. Routines
                routine_files = [n for n in names if n.startswith("routines/") and n.endswith(".json")]
                existing_routines_by_name = {r["name"]: r for r in self.db.list_routines(include_deleted=True, limit=10000)}

                for rf in routine_files:
                    r = json.loads(zf.read(rf).decode("utf-8"))
                    name = r.get("name") or "Imported Routine"
                    if name in existing_routines_by_name:
                        conflicts += 1
                        if conflict == "skip":
                            continue
                        elif conflict == "rename":
                            r["name"] = f"Imported {name}"
                            r["routine_id"] = __import__("relay.util", fromlist=["new_job_id"]).new_job_id()
                            self.db.create_routine(r)
                            imported_routines += 1
                        elif conflict == "overwrite":
                            exist_id = existing_routines_by_name[name]["routine_id"]
                            self.db.update_routine(exist_id, **{k: v for k, v in r.items() if k not in {"routine_id", "created_at", "updated_at"}})
                            imported_routines += 1
                    else:
                        self.db.create_routine(r)
                        imported_routines += 1

                return {
                    "ok": True,
                    "imported_tasks": imported_tasks,
                    "imported_projects": imported_projects,
                    "imported_routines": imported_routines,
                    "conflicts": conflicts,
                }
        except zipfile.BadZipFile as exc:
            raise RelayError("IMPORT_ARCHIVE_INVALID", "File is not a valid zip archive.") from exc
