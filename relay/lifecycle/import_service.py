from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..errors import RelayError
from ..target_workspace import is_within, safe_resolve
from ..util import new_artifact_uid, new_job_id


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
                expected_files = manifest.get("files")
                expected_hashes = manifest.get("sha256")
                if not isinstance(expected_files, list) or not isinstance(expected_hashes, dict):
                    raise RelayError("IMPORT_ARCHIVE_INVALID", "Archive manifest is missing file hashes")
                if len(names) != len(set(names)):
                    raise RelayError("IMPORT_ARCHIVE_INVALID", "Archive contains duplicate entries")
                for name in expected_files:
                    if name not in names:
                        raise RelayError("IMPORT_ARCHIVE_INVALID", f"Archive entry is missing: {name}")
                    actual = hashlib.sha256(zf.read(name)).hexdigest()
                    if actual != expected_hashes.get(name):
                        raise RelayError("IMPORT_ARCHIVE_INVALID", f"Archive hash mismatch: {name}")
                for meta_name in (n for n in names if n.startswith("artifacts/") and n.endswith(".meta.json")):
                    relative_path = Path(str(json.loads(zf.read(meta_name))["relative_path"]))
                    if relative_path.is_absolute() or ".." in relative_path.parts:
                        raise RelayError(
                            "IMPORT_ARCHIVE_INVALID",
                            f"Artifact path is outside artifact root: {relative_path}",
                        )

                imported_tasks = 0
                imported_projects = 0
                imported_routines = 0
                imported_runs = 0
                imported_artifacts = 0
                conflicts = 0

                # 1. Tasks
                task_files = [n for n in names if n.startswith("tasks/") and n.endswith(".json")]
                existing_tasks_by_name = {t["name"]: t for t in self.db.list_tasks(limit=10000)}
                task_id_map: dict[str, str] = {}

                for tf in task_files:
                    t = json.loads(zf.read(tf).decode("utf-8"))
                    source_task_id = str(t["task_id"])
                    name = t.get("name") or "Imported Task"
                    if name in existing_tasks_by_name:
                        conflicts += 1
                        if conflict == "skip":
                            task_id_map[source_task_id] = existing_tasks_by_name[name]["task_id"]
                            continue
                        elif conflict == "rename":
                            t["name"] = f"Imported {name}"
                            t["task_id"] = new_job_id()
                            self.db.create_task(t)
                            task_id_map[source_task_id] = t["task_id"]
                            imported_tasks += 1
                        elif conflict == "overwrite":
                            exist_id = existing_tasks_by_name[name]["task_id"]
                            self.db.update_task(
                                exist_id,
                                **{
                                    k: v
                                    for k, v in t.items()
                                    if k not in {"task_id", "version", "created_at", "updated_at"}
                                },
                            )
                            task_id_map[source_task_id] = exist_id
                            imported_tasks += 1
                    else:
                        # Direct import
                        self.db.create_task(t)
                        task_id_map[source_task_id] = source_task_id
                        imported_tasks += 1

                # 2. Projects
                project_files = [n for n in names if n.startswith("projects/") and n.endswith(".json")]
                existing_projects_by_name = {
                    p["name"]: p for p in self.db.list_projects(include_deleted=True, limit=10000)
                }
                project_id_map: dict[str, str] = {}

                for pf in project_files:
                    p = json.loads(zf.read(pf).decode("utf-8"))
                    source_project_id = str(p["project_id"])
                    p["definition_json"] = _rewrite_project_definition(p["definition_json"], task_id_map)
                    name = p.get("name") or "Imported Project"
                    if name in existing_projects_by_name:
                        conflicts += 1
                        if conflict == "skip":
                            project_id_map[source_project_id] = existing_projects_by_name[name]["project_id"]
                            continue
                        elif conflict == "rename":
                            p["name"] = f"Imported {name}"
                            p["project_id"] = new_job_id()
                            self.db.create_project(p)
                            project_id_map[source_project_id] = p["project_id"]
                            imported_projects += 1
                        elif conflict == "overwrite":
                            exist_id = existing_projects_by_name[name]["project_id"]
                            self.db.update_project(
                                exist_id,
                                **{
                                    k: v
                                    for k, v in p.items()
                                    if k not in {"project_id", "version", "created_at", "updated_at"}
                                },
                            )
                            project_id_map[source_project_id] = exist_id
                            imported_projects += 1
                    else:
                        self.db.create_project(p)
                        project_id_map[source_project_id] = source_project_id
                        imported_projects += 1

                # 3. Routines
                routine_files = [n for n in names if n.startswith("routines/") and n.endswith(".json")]
                existing_routines_by_name = {
                    r["name"]: r for r in self.db.list_routines(include_deleted=True, limit=10000)
                }

                for rf in routine_files:
                    r = json.loads(zf.read(rf).decode("utf-8"))
                    if r.get("target_type") == "task":
                        r["target_id"] = task_id_map.get(r["target_id"], r["target_id"])
                    elif r.get("target_type") == "project":
                        r["target_id"] = project_id_map.get(r["target_id"], r["target_id"])
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
                            self.db.update_routine(
                                exist_id,
                                **{k: v for k, v in r.items() if k not in {"routine_id", "created_at", "updated_at"}},
                            )
                            imported_routines += 1
                    else:
                        self.db.create_routine(r)
                        imported_routines += 1

                # 4. Task Runs, Artifacts, and lineage are opt-in.
                run_id_map: dict[str, str] = {}
                artifact_uid_map: dict[str, str] = {}
                if include_runs:
                    run_files = sorted(n for n in names if n.startswith("runs/task_run_") and n.endswith(".json"))
                    for run_file in run_files:
                        run = json.loads(zf.read(run_file).decode("utf-8"))
                        old_run_id = str(run["job_id"])
                        new_run_id = old_run_id
                        if self.db.get_job(old_run_id):
                            conflicts += 1
                            if conflict in {"skip", "overwrite"}:
                                continue
                            new_run_id = new_job_id()
                            run["job_id"] = new_run_id
                            run["request_id"] = None
                        self.db.create_job(run)
                        run_id_map[old_run_id] = new_run_id
                        imported_runs += 1

                    meta_files = sorted(n for n in names if n.startswith("artifacts/") and n.endswith(".meta.json"))
                    for meta_file in meta_files:
                        artifact = json.loads(zf.read(meta_file).decode("utf-8"))
                        old_job_id = str(artifact["job_id"])
                        if old_job_id not in run_id_map:
                            continue
                        old_uid = str(artifact.get("artifact_uid") or artifact["artifact_id"])
                        bin_name = f"artifacts/{old_uid}.bin"
                        if bin_name not in names:
                            continue
                        new_uid = old_uid
                        if self.db.artifact_by_uid(new_uid):
                            if conflict != "rename":
                                conflicts += 1
                                continue
                            new_uid = new_artifact_uid()
                        data = zf.read(bin_name)
                        digest = hashlib.sha256(data).hexdigest()
                        if digest != artifact["sha256"] or len(data) != int(artifact["size"]):
                            raise RelayError("IMPORT_ARCHIVE_INVALID", f"Artifact content mismatch: {old_uid}")
                        new_job_id_value = run_id_map[old_job_id]
                        run_artifact_root = safe_resolve(self.config.path_value("artifact_root") / new_job_id_value)
                        destination = run_artifact_root / artifact["relative_path"]
                        destination = safe_resolve(destination)
                        if not is_within(destination, run_artifact_root):
                            raise RelayError(
                                "IMPORT_ARCHIVE_INVALID",
                                f"Artifact path is outside artifact root: {artifact['relative_path']}",
                            )
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(data)
                        restored = {
                            key: value
                            for key, value in artifact.items()
                            if key not in {"artifact_id", "created_at", "job_id", "artifact_uid", "final_path"}
                        }
                        self.db.add_artifact(
                            new_job_id_value,
                            **restored,
                            artifact_uid=new_uid,
                            final_path=str(destination),
                        )
                        artifact_uid_map[old_uid] = new_uid
                        imported_artifacts += 1

                    if "lineage.json" in names:
                        for item in json.loads(zf.read("lineage.json").decode("utf-8")):
                            consumer = run_id_map.get(str(item["consumer_job_id"]))
                            source = run_id_map.get(str(item["source_job_id"]))
                            source_uid = artifact_uid_map.get(str(item["source_artifact_uid"]))
                            if not consumer or not source or not source_uid:
                                continue
                            restored_lineage = {
                                key: value
                                for key, value in item.items()
                                if key
                                not in {
                                    "lineage_id",
                                    "created_at",
                                    "consumer_job_id",
                                    "source_job_id",
                                    "source_artifact_uid",
                                }
                            }
                            self.db.add_lineage(
                                {
                                    **restored_lineage,
                                    "consumer_job_id": consumer,
                                    "source_job_id": source,
                                    "source_artifact_uid": source_uid,
                                }
                            )

                return {
                    "ok": True,
                    "imported_tasks": imported_tasks,
                    "imported_projects": imported_projects,
                    "imported_routines": imported_routines,
                    "imported_runs": imported_runs,
                    "imported_artifacts": imported_artifacts,
                    "conflicts": conflicts,
                }
        except zipfile.BadZipFile as exc:
            raise RelayError("IMPORT_ARCHIVE_INVALID", "File is not a valid zip archive.") from exc


def _rewrite_project_definition(definition_json: str, task_id_map: dict[str, str]) -> str:
    definition = json.loads(definition_json)
    for node in definition.get("nodes", []):
        if node.get("task_id") in task_id_map:
            node["task_id"] = task_id_map[node["task_id"]]
    return json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
