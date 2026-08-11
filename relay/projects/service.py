from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..db import Database
from ..engine import RelayEngine
from ..errors import RelayError
from ..orchestrator.overrides import effective_manifest_entries, parse_step_overrides
from ..util import canonical_json, new_job_id, sha256_file, utc_now
from .models import (
    ProjectSpec,
)

_PROJECT_TERMINAL = {"completed", "failed", "cancelled"}

_ALIAS_PATTERN = re.compile(r"^A[1-9][0-9]*$")


def _now() -> str:
    return utc_now()


class ProjectService:
    def __init__(self, db: Database, engine: RelayEngine):
        self.db = db
        self.engine = engine
        self.engine_db = db

    def create_project(self, definition: dict[str, Any]) -> dict[str, Any]:
        spec = ProjectSpec.from_dict(definition)
        spec.validate(self._task_snapshot, allow_roots=self._delivery_roots())
        project_id = new_job_id()
        project_row = {
            "project_id": project_id,
            "name": spec.name or "Untitled",
            "description": spec.description,
            "project_summary": spec.project_summary or spec.description or spec.name,
            "version": 1,
            "definition_json": spec.to_snapshot(),
        }
        self.db.create_project(project_row)
        return self.db.get_project(project_id)

    def update_project(self, project_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        existing = self.db.get_project(project_id)
        if not existing or existing.get("deleted_at") is not None:
            raise RelayError("PROJECT_NOT_FOUND", f"Project not found: {project_id}")
        spec = ProjectSpec.from_dict(definition)
        spec.validate(self._task_snapshot, allow_roots=self._delivery_roots())
        snapshot = spec.to_snapshot()
        self.db.update_project(
            project_id,
            name=spec.name or existing["name"],
            description=spec.description,
            project_summary=spec.project_summary or existing.get("project_summary") or spec.description or spec.name,
            definition_json=snapshot,
        )
        return self.db.get_project(project_id)

    def soft_delete_project(self, project_id: str) -> bool:
        return self.db.soft_delete_project(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if not project or project.get("deleted_at") is not None:
            raise RelayError("PROJECT_NOT_FOUND", f"Project not found: {project_id}")
        return project

    def list_projects(self, *, name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.list_projects(name=name, limit=limit)

    def _project_spec(self, project_id: str, version: int) -> ProjectSpec:
        definition = self.db.get_project(project_id)
        if not definition:
            raise RelayError("PROJECT_NOT_FOUND", f"Project not found: {project_id}")
        payload = json.loads(definition["definition_json"])
        payload["project_id"] = project_id
        payload["version"] = version
        return ProjectSpec.from_dict(payload)

    def _task_snapshot(self, task_id: str) -> dict[str, Any]:
        return self.engine.load_task_for_snapshot(task_id)

    def _delivery_roots(self) -> list[str]:
        return [str(root) for root in self.engine.config.get("allowed_delivery_roots", [])]

    def _stage_external_input(
        self, project_id: str, project_run_id: str, node_id: str, alias: str, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        snapshot_root = self.engine.config.path_value("input_snapshot_root") / project_run_id
        snapshot_root.mkdir(parents=True, exist_ok=True)
        source = Path(str(artifact["final_path"]))
        if not source.is_file():
            raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact file missing: {artifact['artifact_uid']}")
        size = source.stat().st_size
        digest = sha256_file(source)
        recorded_size = int(artifact.get("size") or 0)
        recorded_digest = str(artifact.get("sha256") or "")
        if recorded_size and size != recorded_size:
            raise RelayError("ARTIFACT_CHANGED", f"Artifact size changed: {artifact['artifact_uid']}")
        if recorded_digest and digest != recorded_digest:
            raise RelayError("ARTIFACT_CHANGED", f"Artifact sha256 changed: {artifact['artifact_uid']}")
        destination = snapshot_root / f"{node_id}__{alias}__{artifact['relative_path']}"
        shutil.copy2(source, destination)
        dest_digest = sha256_file(destination)
        if dest_digest != digest:
            raise RelayError("ARTIFACT_CHANGED", f"Artifact snapshot mismatch: {artifact['artifact_uid']}")
        return {
            "node_id": node_id,
            "to_alias": alias,
            "artifact_uid": artifact["artifact_uid"],
            "source_job_id": artifact["job_id"],
            "source_relative_path": artifact["relative_path"],
            "source_sha256": digest,
            "source_size": size,
            "snapshot_relative_path": str(destination.relative_to(self.engine.config.home)),
            "snapshot_sha256": dest_digest,
            "snapshot_size": destination.stat().st_size,
            "binding_mode": "snapshot",
        }

    def create_project_run(
        self,
        project_id: str,
        *,
        trigger_type: str = "manual",
        submitted_via: str = "cli",
        caller: str = "human",
        external_inputs: list[dict[str, Any]] | None = None,
        routine_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        spec = self._project_spec(project_id, project["version"])
        spec.validate(self._task_snapshot)
        task_snapshots: dict[str, dict[str, Any]] = {}
        for node in spec.nodes:
            task_snapshots[node.task_id] = self._task_snapshot(node.task_id)

        project_run_id = new_job_id()
        external_inputs = external_inputs or []
        staged_inputs: list[dict[str, Any]] = []
        alias_keys: set[tuple[str, str]] = set()
        for input_spec in external_inputs:
            uid = str(input_spec.get("artifact_uid") or "").strip()
            if not uid:
                raise RelayError("INVALID_REQUEST", "External inputs require artifact_uid.")
            artifact = self.engine.db.artifact_by_uid(uid)
            if not artifact:
                raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {uid}")
            node_id = str(input_spec["node_id"])
            alias = str(input_spec["to_alias"])
            if not _ALIAS_PATTERN.match(alias):
                raise RelayError("PROJECT_INVALID", f"External input alias invalid: {alias}")
            key = (node_id, alias)
            if key in alias_keys:
                raise RelayError("PROJECT_INPUT_CONFLICT", f"Duplicate external input ({key[0]}, {key[1]})")
            alias_keys.add(key)
            target_node = next((n for n in spec.nodes if n.node_id == node_id), None)
            if not target_node:
                raise RelayError("PROJECT_INVALID", f"External input targets unknown node: {node_id}")
            if alias in {c.to_alias for c in spec.connections if c.to_node == node_id}:
                raise RelayError(
                    "PROJECT_INPUT_CONFLICT", f"External input collides with connection alias ({node_id}, {alias})"
                )
            staged_inputs.append(self._stage_external_input(project_id, project_run_id, node_id, alias, artifact))

        project_snapshot = {
            "project_id": project_id,
            "project_version": project["version"],
            "project_summary": project.get("project_summary") or project.get("description") or project.get("name"),
            "project_definition": json.loads(project["definition_json"]),
            "task_snapshots": task_snapshots,
            "external_inputs": staged_inputs,
            "failure_policy": spec.failure_policy,
            "output_selection": list(spec.output_selection.items),
        }

        self.db.create_project_run(
            {
                "project_run_id": project_run_id,
                "project_id": project_id,
                "project_version": project["version"],
                "project_snapshot_json": canonical_json(project_snapshot),
                "status": "running",
                "trigger_type": trigger_type,
                "submitted_via": submitted_via,
            }
        )

        if routine_id:
            self.db.update_project_run(project_run_id, routine_id=routine_id)

        steps: list[dict[str, Any]] = []
        external_by_node: dict[str, list[dict[str, Any]]] = {}
        for staged in staged_inputs:
            external_by_node.setdefault(staged["node_id"], []).append(staged)

        successors: dict[str, list[str]] = {n.node_id: [] for n in spec.nodes}
        for conn in spec.connections:
            successors[conn.from_node].append(conn.to_node)
        for nid in successors:
            successors[nid].sort()

        # Determine step status: external-binding or no-dependency -> ready; else pending.
        inputs_by_node: dict[str, list[dict[str, Any]]] = {n.node_id: [] for n in spec.nodes}
        for node in spec.nodes:
            connections_to = spec.connections_to(node.node_id)
            for conn in connections_to:
                # connection's UID resolution is owned by runtime; we just record the manifest.
                inputs_by_node[node.node_id].append(
                    {
                        "from_node": conn.from_node,
                        "from_role": conn.from_role,
                        "to_alias": conn.to_alias,
                        "artifact_uid": None,
                        "snapshot": None,
                    }
                )
            inputs_by_node[node.node_id].extend(external_by_node.get(node.node_id, []))

        for node in spec.nodes:
            deps = spec.predecessor_map()[node.node_id]
            has_external = bool(external_by_node.get(node.node_id))
            connection_inputs = [c for c in inputs_by_node[node.node_id] if c.get("artifact_uid") is None]
            if not deps:
                status = "ready"
            elif has_external and not connection_inputs:
                status = "ready"
            else:
                status = "pending"
            step = self.db.create_or_update_project_step(
                {
                    "project_run_id": project_run_id,
                    "node_id": node.node_id,
                    "task_id": task_snapshots[node.task_id]["task_id"],
                    "task_version": task_snapshots[node.task_id]["version"],
                    "status": status,
                    "input_manifest_json": canonical_json(inputs_by_node[node.node_id])
                    if inputs_by_node[node.node_id]
                    else None,
                    "resolved_connections_json": canonical_json([]),
                }
            )
            steps.append(step)

        return {
            "project_run": self.db.get_project_run(project_run_id),
            "steps": steps,
            "project_run_id": project_run_id,
        }

    def get_step_inputs(self, project_run_id: str, node_id: str) -> dict[str, Any]:
        step = self.db.get_project_step(project_run_id, node_id)
        if not step:
            raise RelayError("PROJECT_NOT_FOUND", f"Step not found: {project_run_id}/{node_id}")
        return {
            "task_run_id": step.get("active_task_run_id"),
            "input_manifest_json": step.get("input_manifest_json"),
            "resolved_connections_json": step.get("resolved_connections_json"),
        }

    def resolve_step_inputs(self, project_run_id: str, node_id: str) -> list[dict[str, Any]]:
        project_run = self.db.get_project_run(project_run_id)
        if not project_run:
            raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
        snapshot = json.loads(project_run["project_snapshot_json"])
        step = self.db.get_project_step(project_run_id, node_id) or {}
        manifest = json.loads(step.get("input_manifest_json") or "[]")
        overrides = parse_step_overrides(step.get("step_overrides_json"))
        manifest = effective_manifest_entries(manifest, overrides.get("connection_overrides"))
        resolved: list[dict[str, Any]] = []
        for entry in manifest:
            if entry.get("artifact_uid") and entry.get("snapshot"):
                enriched = dict(entry)
                enriched.setdefault("from_node", entry.get("from_node"))
                enriched.setdefault("from_role", entry.get("from_role"))
                resolved.append(enriched)
                continue
            # External input lookup.
            external = next(
                (
                    e
                    for e in snapshot.get("external_inputs", [])
                    if e["node_id"] == node_id and e["to_alias"] == entry["to_alias"]
                ),
                None,
            )
            if external:
                enriched = dict(external)
                enriched.setdefault("from_node", None)
                enriched.setdefault("from_role", "external")
                resolved.append(enriched)
                continue
            # Connection: find source Task Run's Artifact matching from_role.
            source_node = entry["from_node"]
            from_role = entry["from_role"]
            source_step = self.db.get_project_step(project_run_id, source_node)
            if not source_step or not source_step.get("active_task_run_id"):
                raise RelayError(
                    "PROJECT_ARTIFACT_MISSING",
                    f"Upstream Task Run missing for {source_node}->{node_id}.{entry['to_alias']}",
                )
            artifacts = self.engine.db.artifacts_for_job(source_step["active_task_run_id"])
            matches = [a for a in artifacts if a.get("role") == from_role]
            edited_uid = next(
                (
                    approval.get("edited_artifact_uid")
                    for approval in reversed(self.db.list_approvals(project_run_id))
                    if approval["node_id"] == source_node
                    and approval["status"] == "approved"
                    and approval.get("edited_artifact_uid")
                ),
                None,
            )
            if edited_uid:
                edited = self.engine.db.artifact_by_uid(edited_uid)
                if edited and edited.get("role") == from_role:
                    matches = [edited]
            if not matches:
                raise RelayError(
                    "PROJECT_ARTIFACT_MISSING", f"Source Artifact for role {from_role} missing in {source_node}"
                )
            if len(matches) > 1:
                raise RelayError(
                    "PROJECT_ARTIFACT_AMBIGUOUS", f"Multiple source Artifacts for role {from_role} in {source_node}"
                )
            src = matches[0]
            snapshot_staged = self._stage_external_input(
                snapshot["project_id"], project_run_id, node_id, entry["to_alias"], src
            )
            enriched = dict(snapshot_staged)
            enriched["from_node"] = source_node
            enriched["from_role"] = from_role
            resolved.append(enriched)
        return resolved

    def _project_spec_from_snapshot(self, snapshot: dict[str, Any]) -> ProjectSpec:
        return ProjectSpec.from_dict(snapshot["project_definition"])

    def retry_project_run(
        self,
        project_run_id: str,
        *,
        from_node: str | None = None,
        worker: str | None = None,
    ) -> dict[str, Any]:
        run = self.db.get_project_run(project_run_id)
        if not run:
            raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
        if run["status"] not in {"failed"}:
            raise RelayError("PROJECT_RETRY_INVALID", f"Cannot retry run in status {run['status']}")
        steps = self.db.list_project_steps(project_run_id)
        if not steps:
            raise RelayError("PROJECT_RETRY_INVALID", "No steps to retry.")
        failed = [s for s in steps if s["status"] == "failed"]
        if not failed and not from_node:
            raise RelayError("PROJECT_RETRY_INVALID", "No failed steps to retry.")
        target_node = from_node or failed[0]["node_id"]
        # Reset descendants to pending; keep upstream successful steps and their snapshots.
        descendants = self._collect_descendants(project_run_id, target_node, set(s["node_id"] for s in steps))
        for s in steps:
            if s["node_id"] == target_node or s["node_id"] in descendants:
                payload = {"status": "pending", "active_task_run_id": None, "error_code": None, "error_message": None}
                if s["node_id"] == target_node and worker is not None:
                    payload["step_overrides_json"] = canonical_json({"worker_override": worker})
                self.db.update_project_step(project_run_id, s["node_id"], **payload)
            elif s["status"] == "blocked":
                self.db.update_project_step(
                    project_run_id, s["node_id"], status="pending", error_code=None, error_message=None
                )
        self.db.update_project_run(project_run_id, status="running", completed_at=None, started_at=None)
        return {"project_run": self.db.get_project_run(project_run_id), "target_node": target_node}

    def _collect_descendants(self, project_run_id: str, node_id: str, all_nodes: set[str]) -> set[str]:
        project_run = self.db.get_project_run(project_run_id)
        if not project_run:
            return set()
        snapshot = json.loads(project_run["project_snapshot_json"])
        spec = ProjectSpec.from_dict(snapshot["project_definition"])
        adjacency: dict[str, list[str]] = {n.node_id: [] for n in spec.nodes}
        for conn in spec.connections:
            adjacency.setdefault(conn.from_node, []).append(conn.to_node)
        result: set[str] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            for nxt in adjacency.get(current, []):
                if nxt not in result:
                    result.add(nxt)
                    stack.append(nxt)
        result &= all_nodes
        return result

    def _pr_id_for_descendants(self, project_run_id: str) -> str:
        return project_run_id

    def cancel_project_run(self, project_run_id: str) -> dict[str, Any]:
        run = self.db.get_project_run(project_run_id)
        if not run:
            raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
        if run["status"] in _PROJECT_TERMINAL:
            raise RelayError("PROJECT_RUN_TERMINAL", f"Run already terminal: {run['status']}")
        self.db.update_project_run(project_run_id, status="cancelled", completed_at=_now())
        steps = self.db.list_project_steps(project_run_id)
        for step in steps:
            if step["status"] not in {"completed", "failed"}:
                self.db.update_project_step(project_run_id, step["node_id"], status="cancelled")
        return self.db.get_project_run(project_run_id)

    def project_run_receipt(self, project_run_id: str) -> dict[str, Any]:
        run = self.db.get_project_run(project_run_id)
        if not run:
            raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")
        steps = self.db.list_project_steps(project_run_id)
        events_by_node: dict[str, dict[str, Any]] = {}
        for event in self.db.list_project_run_events(project_run_id):
            if event.get("node_id") and event.get("kind") in {"decision", "report", "fallback"}:
                events_by_node[event["node_id"]] = event  # last one wins; events are seq-ordered
        step_receipts = []
        for s in steps:
            step_runs = self.db.list_project_step_runs(project_run_id, s["node_id"])
            resolved = json.loads(s.get("resolved_connections_json") or "[]")
            if isinstance(resolved, dict):
                resolved = []
            last_event = events_by_node.get(s["node_id"])
            step_receipts.append(
                {
                    "node_id": s["node_id"],
                    "task_id": s["task_id"],
                    "task_version": s["task_version"],
                    "status": s["status"],
                    "active_task_run_id": s.get("active_task_run_id"),
                    "task_runs": step_runs,
                    "resolved_inputs": resolved,
                    "error_code": s.get("error_code"),
                    "error_message": s.get("error_message"),
                    "step_overrides": parse_step_overrides(s.get("step_overrides_json")),
                    "orchestrator_summary": last_event["summary"] if last_event else None,
                }
            )
        snapshot = json.loads(run["project_snapshot_json"])
        return {
            "project_run_id": project_run_id,
            "project_id": run["project_id"],
            "project_version": run["project_version"],
            "status": run["status"],
            "trigger_type": run["trigger_type"],
            "submitted_via": run["submitted_via"],
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "external_inputs": snapshot.get("external_inputs", []),
            "steps": step_receipts,
            "warnings": json.loads(run.get("warnings_json") or "[]"),
            "final_artifact_ids": json.loads(run.get("final_artifact_ids_json") or "[]"),
        }

    def partial_reexecute(
        self,
        project_run_id: str,
        from_node: str,
        cascade: bool = True,
        worker: str | None = None,
        instruction_addendum: str | None = None,
    ) -> dict[str, Any]:
        run = self.db.get_project_run(project_run_id)
        if not run:
            raise RelayError("PROJECT_RUN_NOT_FOUND", f"Project run not found: {project_run_id}")

        steps = self.db.list_project_steps(project_run_id)
        step_nodes = {s["node_id"] for s in steps}
        if from_node not in step_nodes:
            raise RelayError("PARTIAL_REEXECUTE_INVALID", f"Node not found in project run: {from_node}")

        targets = {from_node}
        if cascade:
            targets |= self._collect_descendants(project_run_id, from_node, step_nodes)

        for s in steps:
            if s["node_id"] in targets:
                payload = {"status": "pending", "active_task_run_id": None, "error_code": None, "error_message": None}
                if s["node_id"] == from_node and (worker is not None or instruction_addendum is not None):
                    overrides: dict[str, Any] = {}
                    if worker is not None:
                        overrides["worker_override"] = worker
                    if instruction_addendum is not None and instruction_addendum.strip():
                        overrides["instruction_addendum"] = instruction_addendum.strip()
                    if overrides:
                        payload["step_overrides_json"] = canonical_json(overrides)
                self.db.update_project_step(project_run_id, s["node_id"], **payload)

        self.db.update_project_run(project_run_id, status="running", completed_at=None, started_at=None)
        return {
            "ok": True,
            "project_run": self.db.get_project_run(project_run_id),
            "target_node": from_node,
            "reexecuted_nodes": sorted(targets),
        }
