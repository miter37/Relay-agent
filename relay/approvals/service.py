from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..target_workspace import safe_resolve
from ..util import new_artifact_uid, new_job_id, sha256_file, utc_now


class ApprovalService:
    def __init__(self, db: Database, engine: RelayEngine, config: Config):
        self.db = db
        self.engine = engine
        self.config = config

    def create_pending_approval(self, project_run_id: str, node_id: str) -> dict[str, Any]:
        step = self.db.get_project_step(project_run_id, node_id)
        if not step:
            raise RelayError("PROJECT_NOT_FOUND", f"Step not found: {project_run_id}/{node_id}")
        approval_id = new_job_id()
        token = new_job_id()
        self.db.create_approval(
            {
                "approval_id": approval_id,
                "project_run_id": project_run_id,
                "node_id": node_id,
                "token": token,
                "status": "pending",
            }
        )
        self.db.update_project_step(project_run_id, node_id, status="awaiting_approval")
        return self.db.get_approval(token)  # type: ignore[return-value]

    def _get_pending_approval(self, project_run_id: str, token: str) -> dict[str, Any]:
        app = self.db.get_approval(token)
        if not app or app["project_run_id"] != project_run_id:
            raise RelayError("APPROVAL_NOT_FOUND", f"Approval not found for token: {token}")
        if app["status"] != "pending":
            raise RelayError("APPROVAL_ALREADY_DECIDED", f"Approval already decided with status: {app['status']}")
        return app

    def approve(self, project_run_id: str, token: str, reviewer: str = "human") -> dict[str, Any]:
        app = self._get_pending_approval(project_run_id, token)
        now = utc_now()
        self.db.update_approval(token, status="approved", reviewer=reviewer, decided_at=now)
        self.db.update_project_step(project_run_id, app["node_id"], status="completed", completed_at=now)
        self.deliver(project_run_id, token)
        return {"approval": self.db.get_approval(token)}

    def approve_with_edits(
        self,
        project_run_id: str,
        token: str,
        reviewer: str,
        edit_file_path: str,
        role: str = "output",
    ) -> dict[str, Any]:
        app = self._get_pending_approval(project_run_id, token)
        src_path = safe_resolve(Path(edit_file_path))
        if not src_path.is_file():
            raise RelayError("INVALID_REQUEST", f"Edited file not found: {edit_file_path}")

        step = self.db.get_project_step(project_run_id, app["node_id"])
        job_id = step["active_task_run_id"] if step else None
        if not job_id:
            raise RelayError("INVALID_REQUEST", "No active Task Run for checkpoint step.")

        artifact_dir = self.config.path_value("artifact_root") / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        dest_file = artifact_dir / f"edited_{src_path.name}"
        shutil.copy2(src_path, dest_file)

        size = dest_file.stat().st_size
        digest = sha256_file(dest_file)
        edited_uid = new_artifact_uid()

        self.db.add_artifact(
            job_id,
            relative_path=dest_file.name,
            final_path=str(dest_file),
            mime_type="text/plain",
            size=size,
            sha256=digest,
            artifact_uid=edited_uid,
            role=role,
            producer_attempt_id=None,
        )

        now = utc_now()
        self.db.update_approval(
            token, status="approved", reviewer=reviewer, edited_artifact_uid=edited_uid, decided_at=now
        )
        self.db.update_project_step(project_run_id, app["node_id"], status="completed", completed_at=now)
        self.deliver(project_run_id, token, edited_artifact_uid=edited_uid)
        return {"approval": self.db.get_approval(token)}

    def reject(self, project_run_id: str, token: str, reviewer: str = "human", reason: str = "") -> dict[str, Any]:
        app = self._get_pending_approval(project_run_id, token)
        now = utc_now()
        self.db.update_approval(token, status="rejected", reviewer=reviewer, reason=reason, decided_at=now)
        self.db.update_project_step(
            project_run_id,
            app["node_id"],
            status="failed",
            error_code="APPROVAL_REJECTED",
            error_message=reason or "Rejected by human reviewer",
            completed_at=now,
        )
        self.db.update_project_run(project_run_id, status="failed", completed_at=now)
        return {"approval": self.db.get_approval(token)}

    def deliver(self, project_run_id: str, token: str, edited_artifact_uid: str | None = None) -> list[dict[str, Any]]:
        app = self.db.get_approval(token)
        if not app:
            raise RelayError("APPROVAL_NOT_FOUND", f"Approval not found: {token}")

        prun = self.db.get_project_run(project_run_id)
        if not prun:
            raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")

        snapshot = json.loads(prun["project_snapshot_json"])
        nodes = snapshot.get("project_definition", {}).get("nodes", [])
        node_def = next((n for n in nodes if n["node_id"] == app["node_id"]), None)
        if not node_def or not node_def.get("checkpoint"):
            return []

        deliver_to = node_def["checkpoint"].get("deliver_to") or []
        step = self.db.get_project_step(project_run_id, app["node_id"])
        job_id = step["active_task_run_id"] if step else None

        # Determine source artifact
        artifact = None
        if edited_artifact_uid:
            artifact = self.db.artifact_by_uid(edited_artifact_uid)
        elif job_id:
            artifacts = self.db.artifacts_for_job(job_id)
            if artifacts:
                artifact = artifacts[0]

        if not artifact:
            raise RelayError("ARTIFACT_NOT_FOUND", "No artifact available to deliver.")

        source_file = safe_resolve(Path(str(artifact["final_path"])))
        if not source_file.is_file():
            raise RelayError("ARTIFACT_NOT_FOUND", f"Source artifact file missing: {source_file}")

        deliveries = []
        for item in deliver_to:
            kind = item.get("kind", "folder")
            target_str = item.get("path")
            if not target_str:
                continue

            target_path = safe_resolve(Path(target_str))

            # Determine target file destination
            if target_path.suffix:
                dest_file = target_path
                dest_dir = target_path.parent
            else:
                dest_dir = target_path
                dest_file = target_path / source_file.name

            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, dest_file)

            delivery_id = new_job_id()
            delivery_row = {
                "delivery_id": delivery_id,
                "project_run_id": project_run_id,
                "approval_id": app["approval_id"],
                "kind": kind,
                "target_path": str(dest_file),
                "artifact_uid": artifact["artifact_uid"],
                "status": "completed",
            }
            self.db.create_delivery(delivery_row)
            deliveries.append(delivery_row)

        return deliveries
