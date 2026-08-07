from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from ..errors import RelayError
from ..util import canonical_json
from ..validation import normalize_summary

_ALIAS_PATTERN = re.compile(r"^A[1-9][0-9]*$")
_POLICY_VALUES = {"stop"}

# Machine-readable contract for `relay project schema`. Callers that only have the
# CLI cannot read this module, and the binding rules below are enforced at run time
# rather than at registration, so they have to be stated explicitly.
PROJECT_DEFINITION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Relay Project definition",
    "type": "object",
    "required": ["name", "nodes"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "project_summary": {
            "type": "string",
            "maxLength": 500,
            "description": "Shown in `relay catalog projects`; make it specific enough to choose by.",
        },
        "failure_policy": {"type": "string", "enum": sorted(_POLICY_VALUES), "default": "stop"},
        "nodes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["node_id", "task_id"],
                "properties": {
                    "node_id": {"type": "string", "minLength": 1, "description": "Unique within the Project."},
                    "task_id": {"type": "string", "description": "An existing registered Task."},
                    "checkpoint": {
                        "type": "object",
                        "description": "Pause for human approval after this node.",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "deliver_to": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["kind", "path"],
                                    "properties": {
                                        "kind": {"type": "string", "enum": ["folder"]},
                                        "path": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "connections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from_node", "from_role", "to_node", "to_alias"],
                "properties": {
                    "from_node": {"type": "string"},
                    "from_role": {
                        "type": "string",
                        "description": "Artifact role produced by from_node. See rules.artifact_roles.",
                    },
                    "to_node": {"type": "string"},
                    "to_alias": {"type": "string", "pattern": _ALIAS_PATTERN.pattern},
                },
            },
        },
        "output_selection": {
            "type": "array",
            "description": "Final deliverables of the Project.",
            "items": {
                "type": "object",
                "required": ["node_id", "role"],
                "properties": {"node_id": {"type": "string"}, "role": {"type": "string"}},
            },
        },
    },
}

PROJECT_DEFINITION_RULES: dict[str, Any] = {
    "artifact_roles": {
        "result": "Relay labels each Run's result file `result`. Reserved: a Worker cannot declare it. Always exactly one per successful Run, so it is the safest thing to bind.",
        "declared": "A Worker may set `role` on an entry in its result JSON `artifacts` array. Lowercase, ^[a-z][a-z0-9_-]{0,31}$.",
        "output": "Default role for any produced file that declared none.",
    },
    "exactly_one_match": (
        "Every connection and every output_selection entry resolves by (node, role) and must match "
        "exactly one Artifact. Zero matches fail with PROJECT_ARTIFACT_MISSING; two or more fail with "
        "PROJECT_ARTIFACT_AMBIGUOUS. A node that emits several files consumed separately must give each "
        "a distinct role."
    ),
    "input_delivery": (
        "A bound Artifact arrives in the consuming Task Run under input/ named "
        "{node_id}__{alias}__{source_relative_path}, and is listed by alias in the request's "
        "Artifact Inputs section. The filename is not the alias."
    ),
    "validated_at_registration": [
        "PROJECT_INVALID: no nodes, blank node_id, duplicate node_id, to_alias not matching A1/A2/..., "
        "output_selection referencing an unknown node, unknown failure_policy",
        "PROJECT_TASK_MISSING: node task_id is not a registered Task",
        "PROJECT_CYCLE: the connection graph is not a DAG",
        "PROJECT_INPUT_CONFLICT: two connections target the same (to_node, to_alias)",
        "DELIVERY_PATH_NOT_ALLOWED: checkpoint deliver_to path outside allowed_delivery_roots",
    ],
    "validated_at_run_time": [
        "PROJECT_ARTIFACT_MISSING / PROJECT_ARTIFACT_AMBIGUOUS: see exactly_one_match",
        "ARTIFACT_CHANGED: a bound Artifact changed size or sha256 since it was produced",
    ],
}


@dataclass(slots=True)
class ProjectNode:
    node_id: str
    task_id: str
    checkpoint: dict[str, Any] | None = None


@dataclass(slots=True)
class ProjectConnection:
    from_node: str
    from_role: str
    to_node: str
    to_alias: str


@dataclass(slots=True)
class ProjectOutputSelection:
    items: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ProjectSpec:
    nodes: list[ProjectNode]
    connections: list[ProjectConnection]
    output_selection: ProjectOutputSelection
    failure_policy: str = "stop"
    notification_policy: dict[str, Any] | None = None
    description: str | None = None
    project_summary: str | None = None
    name: str | None = None
    project_id: str | None = None
    version: int = 1

    def to_snapshot(self) -> str:
        return canonical_json(self._to_dict())

    def _to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "project_summary": self.project_summary,
            "version": self.version,
            "failure_policy": self.failure_policy,
            **({"notification_policy": self.notification_policy} if self.notification_policy else {}),
            "nodes": [
                {"node_id": n.node_id, "task_id": n.task_id, **({"checkpoint": n.checkpoint} if n.checkpoint else {})}
                for n in self.nodes
            ],
            "connections": [
                {
                    "from_node": c.from_node,
                    "from_role": c.from_role,
                    "to_node": c.to_node,
                    "to_alias": c.to_alias,
                }
                for c in self.connections
            ],
            "output_selection": [
                {"node_id": item["node_id"], "role": item["role"]} for item in self.output_selection.items
            ],
        }

    def validate(
        self, task_lookup: Callable[[str], dict[str, Any] | None], allow_roots: Iterable[str] | None = None
    ) -> None:
        if self.project_summary is not None:
            self.project_summary = normalize_summary(
                self.project_summary,
                max_chars=500,
                field="project_summary",
                error_code="PROJECT_INVALID",
            )
        if not self.nodes:
            raise RelayError("PROJECT_INVALID", "Project must declare at least one node.")
        node_ids: list[str] = []
        for node in self.nodes:
            if not node.node_id.strip():
                raise RelayError("PROJECT_INVALID", "Project node node_id must be non-empty.")
            if node.node_id in node_ids:
                raise RelayError("PROJECT_INVALID", f"Duplicate project node id: {node.node_id}")
            node_ids.append(node.node_id)
            if not task_lookup(node.task_id):
                raise RelayError("PROJECT_TASK_MISSING", f"Task not found: {node.task_id}")
            if node.checkpoint:
                if not isinstance(node.checkpoint, dict):
                    raise RelayError("PROJECT_INVALID", f"Node checkpoint must be an object: {node.node_id}")
                deliver_to = node.checkpoint.get("deliver_to") or []
                if not isinstance(deliver_to, list):
                    raise RelayError("PROJECT_INVALID", f"deliver_to must be a list in node {node.node_id}")
                for item in deliver_to:
                    if not isinstance(item, dict):
                        raise RelayError("PROJECT_INVALID", f"deliver_to item must be an object in node {node.node_id}")
                    kind = str(item.get("kind") or "").strip()
                    if kind != "folder":
                        raise RelayError("DELIVERY_KIND_UNSUPPORTED", f"Unsupported delivery kind: {kind}")
                    target_path = str(item.get("path") or "").strip()
                    if not target_path:
                        raise RelayError("PROJECT_INVALID", f"Delivery target path missing in node {node.node_id}")
                    if allow_roots is not None:
                        from pathlib import Path

                        from ..target_workspace import is_within, safe_resolve

                        resolved = safe_resolve(Path(target_path))
                        if not any(is_within(resolved, Path(r)) for r in allow_roots):
                            raise RelayError(
                                "DELIVERY_PATH_NOT_ALLOWED", f"Delivery path is not in allow-list: {target_path}"
                            )
        node_set = set(node_ids)
        for conn in self.connections:
            if conn.from_node not in node_set:
                raise RelayError("PROJECT_INVALID", f"Connection references unknown from_node: {conn.from_node}")
            if conn.to_node not in node_set:
                raise RelayError("PROJECT_INVALID", f"Connection references unknown to_node: {conn.to_node}")
            if conn.from_node == conn.to_node:
                raise RelayError("PROJECT_INVALID", f"Self-loop connection at {conn.from_node}")
            if not conn.from_role.strip():
                raise RelayError("PROJECT_INVALID", "Connection from_role must be non-empty.")
            if not _ALIAS_PATTERN.match(conn.to_alias):
                raise RelayError("PROJECT_INVALID", f"Connection to_alias must match A1 pattern: {conn.to_alias}")
        seen: set[tuple[str, str]] = set()
        for conn in self.connections:
            key = (conn.to_node, conn.to_alias)
            if key in seen:
                raise RelayError(
                    "PROJECT_INPUT_CONFLICT",
                    f"Two inputs target the same ({conn.to_node}, {conn.to_alias})",
                )
            seen.add(key)
        self._topological_order(node_ids)
        for item in self.output_selection.items:
            nid = item.get("node_id", "")
            role = item.get("role", "")
            if nid not in node_set:
                raise RelayError("PROJECT_INVALID", f"output_selection references unknown node: {nid}")
            if not role.strip():
                raise RelayError("PROJECT_INVALID", "output_selection role must be non-empty.")
        if self.failure_policy not in _POLICY_VALUES:
            raise RelayError("PROJECT_INVALID", f"Unknown failure_policy: {self.failure_policy}")

    def _topological_order(self, node_ids: list[str]) -> list[str]:
        indegree: dict[str, int] = {n: 0 for n in node_ids}
        adjacency: dict[str, list[str]] = {n: [] for n in node_ids}
        for conn in self.connections:
            adjacency.setdefault(conn.from_node, []).append(conn.to_node)
            indegree[conn.to_node] = indegree.get(conn.to_node, 0) + 1
        ordered: list[str] = []
        ready = sorted(n for n, d in indegree.items() if d == 0)
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for neighbor in sorted(adjacency.get(current, [])):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    ready.append(neighbor)
            ready.sort()
        if len(ordered) != len(node_ids):
            raise RelayError("PROJECT_CYCLE", "Project connection graph contains a cycle.")
        return ordered

    def topological_order(self) -> list[str]:
        return self._topological_order([n.node_id for n in self.nodes])

    def predecessor_map(self) -> dict[str, list[str]]:
        pred: dict[str, list[str]] = {n.node_id: [] for n in self.nodes}
        for conn in self.connections:
            pred.setdefault(conn.to_node, []).append(conn.from_node)
        for node_id in pred:
            pred[node_id].sort()
        return pred

    def dependents_map(self) -> dict[str, list[str]]:
        dep: dict[str, list[str]] = {n.node_id: [] for n in self.nodes}
        for conn in self.connections:
            dep.setdefault(conn.from_node, []).append(conn.to_node)
        for node_id in dep:
            dep[node_id].sort()
        return dep

    def root_nodes(self) -> list[str]:
        indegree: dict[str, int] = {n.node_id: 0 for n in self.nodes}
        for conn in self.connections:
            indegree[conn.to_node] = indegree.get(conn.to_node, 0) + 1
        return sorted(n for n, d in indegree.items() if d == 0)

    def connections_to(self, node_id: str) -> list[ProjectConnection]:
        return [c for c in self.connections if c.to_node == node_id]

    def connections_from(self, node_id: str) -> list[ProjectConnection]:
        return [c for c in self.connections if c.from_node == node_id]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProjectSpec:
        import json

        notification_policy = payload.get("notification_policy") or payload.get("notification_policy_json")
        if isinstance(notification_policy, str):
            notification_policy = json.loads(notification_policy)
        nodes = [
            ProjectNode(node_id=str(n["node_id"]), task_id=str(n["task_id"]), checkpoint=n.get("checkpoint"))
            for n in payload.get("nodes", [])
        ]
        connections = [
            ProjectConnection(
                from_node=str(c["from_node"]),
                from_role=str(c["from_role"]),
                to_node=str(c["to_node"]),
                to_alias=str(c["to_alias"]),
            )
            for c in payload.get("connections", [])
        ]
        output_items = [
            {"node_id": str(o["node_id"]), "role": str(o["role"])} for o in payload.get("output_selection", [])
        ]
        return cls(
            nodes=nodes,
            connections=connections,
            output_selection=ProjectOutputSelection(items=output_items),
            failure_policy=str(payload.get("failure_policy", "stop")),
            notification_policy=notification_policy,
            description=payload.get("description"),
            project_summary=payload.get("project_summary"),
            name=payload.get("name"),
            project_id=payload.get("project_id"),
            version=int(payload.get("version", 1)),
        )


def collect_required_bindings(node_id: str, connections: Iterable[ProjectConnection]) -> list[ProjectConnection]:
    return [c for c in connections if c.to_node == node_id]
