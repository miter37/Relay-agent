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

# Orchestrator authority is a strict subset of what a human already does through the
# CLI/GUI (retry, worker swap, instruction addendum, connection/output role rebind) and
# never escapes the Run it is attached to; see docs/superpowers/plans/2026-08-10-project-orchestrator.md.
ORCHESTRATOR_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "worker": None,
    "model": None,
    "profile": None,
    "max_repair_attempts_per_node": 2,
    "max_repair_attempts_per_run": 6,
    "max_llm_calls_per_run": 8,
}
_ORCHESTRATOR_KEYS = set(ORCHESTRATOR_DEFAULTS)
_ORCHESTRATOR_BUDGET_KEYS = {
    "max_repair_attempts_per_node",
    "max_repair_attempts_per_run",
    "max_llm_calls_per_run",
}

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
        "delivery": {
            "type": "object",
            "description": "Optional final-output folder delivery after all review gates pass.",
            "required": ["kind", "path"],
            "properties": {"kind": {"type": "string", "enum": ["folder"]}, "path": {"type": "string"}},
        },
        "nodes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["node_id"],
                "anyOf": [{"required": ["task_id"]}, {"required": ["type", "wait"]}],
                "properties": {
                    "node_id": {"type": "string", "minLength": 1, "description": "Unique within the Project."},
                    "task_id": {"type": "string", "description": "An existing registered Task."},
                    "type": {"type": "string", "enum": ["task", "wait"]},
                    "wait": {
                        "type": "object",
                        "description": "Optional durable pause node.",
                        "properties": {
                            "mode": {"type": "string", "enum": ["duration", "manual"]},
                            "seconds": {"type": "integer", "minimum": 1, "maximum": 31536000},
                        },
                    },
                    "checkpoint": {
                        "type": "object",
                        "description": "Pause for human or Orchestrator review after this node.",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "reviewer": {"type": "string", "enum": ["human", "orchestrator"]},
                            "guidelines": {"type": "string", "maxLength": 8000},
                            "max_reruns": {"type": "integer", "minimum": 0, "maximum": 20, "default": 2},
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
                "required": ["from_node", "to_node"],
                "anyOf": [
                    {"required": ["from_role", "to_alias"]},
                    {"required": ["from_output", "to_input"]},
                ],
                "properties": {
                    "from_node": {"type": "string"},
                    "from_role": {
                        "type": "string",
                        "description": "Artifact role produced by from_node. See rules.artifact_roles.",
                    },
                    "to_node": {"type": "string"},
                    "to_alias": {"type": "string", "pattern": _ALIAS_PATTERN.pattern},
                    "from_output": {
                        "type": "string",
                        "description": "Named Output role. Preferred for new definitions; from_role remains compatible.",
                    },
                    "to_input": {
                        "type": "string",
                        "description": "Named Artifact input. Relay assigns a stable legacy alias for delivery.",
                    },
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
    node_type: str = "task"
    wait: dict[str, Any] | None = None


@dataclass(slots=True)
class ProjectConnection:
    from_node: str
    from_role: str
    to_node: str
    to_alias: str
    from_output: str | None = None
    to_input: str | None = None


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
    orchestrator: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
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
            **({"orchestrator": self.orchestrator} if self.orchestrator else {}),
            **({"delivery": self.delivery} if self.delivery else {}),
            "nodes": [
                {
                    "node_id": n.node_id,
                    **({"task_id": n.task_id} if n.node_type != "wait" else {}),
                    **({"type": n.node_type} if n.node_type != "task" else {}),
                    **({"wait": n.wait} if n.wait else {}),
                    **({"checkpoint": n.checkpoint} if n.checkpoint else {}),
                }
                for n in self.nodes
            ],
            "connections": [
                {
                    "from_node": c.from_node,
                    "from_role": c.from_role,
                    "to_node": c.to_node,
                    "to_alias": c.to_alias,
                    **({"from_output": c.from_output} if c.from_output else {}),
                    **({"to_input": c.to_input} if c.to_input else {}),
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
            if node.node_type not in {"task", "wait"}:
                raise RelayError("PROJECT_INVALID", f"Unknown project node type: {node.node_type}")
            if node.node_type == "wait":
                if node.wait is None or not isinstance(node.wait, dict):
                    raise RelayError("PROJECT_INVALID", f"Wait node requires a wait object: {node.node_id}")
                mode = str(node.wait.get("mode") or "").strip().lower()
                if mode not in {"duration", "manual"}:
                    raise RelayError("PROJECT_INVALID", f"Wait mode must be duration or manual: {node.node_id}")
                if mode == "duration":
                    seconds = node.wait.get("seconds")
                    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 31536000:
                        raise RelayError("PROJECT_INVALID", f"Wait duration must be 1..31536000 seconds: {node.node_id}")
                continue
            if not task_lookup(node.task_id):
                raise RelayError("PROJECT_TASK_MISSING", f"Task not found: {node.task_id}")
            if node.checkpoint:
                if not isinstance(node.checkpoint, dict):
                    raise RelayError("PROJECT_INVALID", f"Node checkpoint must be an object: {node.node_id}")
                deliver_to = node.checkpoint.get("deliver_to") or []
                reviewer = str(node.checkpoint.get("reviewer") or "human")
                if reviewer not in {"human", "orchestrator"}:
                    raise RelayError(
                        "PROJECT_INVALID", f"checkpoint reviewer must be human or orchestrator: {node.node_id}"
                    )
                guidelines = node.checkpoint.get("guidelines")
                if guidelines is not None and (not isinstance(guidelines, str) or len(guidelines) > 8000):
                    raise RelayError("PROJECT_INVALID", f"checkpoint guidelines are invalid: {node.node_id}")
                max_reruns = node.checkpoint.get("max_reruns", 0)
                if not isinstance(max_reruns, int) or isinstance(max_reruns, bool) or not 0 <= max_reruns <= 20:
                    raise RelayError(
                        "PROJECT_INVALID", f"checkpoint max_reruns must be between 0 and 20: {node.node_id}"
                    )
                if reviewer == "orchestrator" and not str(guidelines or "").strip():
                    raise RelayError("PROJECT_INVALID", f"Orchestrator review guidelines are required: {node.node_id}")
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
        seen_inputs: set[tuple[str, str]] = set()
        for conn in self.connections:
            key = (conn.to_node, conn.to_alias)
            if key in seen:
                raise RelayError(
                    "PROJECT_INPUT_CONFLICT",
                    f"Two inputs target the same ({conn.to_node}, {conn.to_alias})",
                )
            seen.add(key)
            if conn.to_input:
                input_key = (conn.to_node, conn.to_input)
                if input_key in seen_inputs:
                    raise RelayError(
                        "PROJECT_INPUT_CONFLICT",
                        f"Two connections target the same named input ({conn.to_node}, {conn.to_input})",
                    )
                seen_inputs.add(input_key)
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
        self._validate_orchestrator()
        self._validate_delivery(allow_roots)

    def _validate_delivery(self, allow_roots: Iterable[str] | None) -> None:
        if self.delivery is None:
            return
        if not isinstance(self.delivery, dict) or str(self.delivery.get("kind") or "") != "folder":
            raise RelayError("DELIVERY_KIND_UNSUPPORTED", "Project delivery currently supports kind=folder only.")
        target_path = str(self.delivery.get("path") or "").strip()
        if not target_path:
            raise RelayError("PROJECT_INVALID", "Project delivery folder path is required.")
        if allow_roots is not None:
            from pathlib import Path

            from ..target_workspace import is_within, safe_resolve

            resolved = safe_resolve(Path(target_path))
            if not any(is_within(resolved, Path(root)) for root in allow_roots):
                raise RelayError("DELIVERY_PATH_NOT_ALLOWED", f"Delivery path is not in allow-list: {target_path}")

    def _validate_orchestrator(self) -> None:
        if self.orchestrator is None:
            return
        if not isinstance(self.orchestrator, dict):
            raise RelayError("PROJECT_INVALID", "orchestrator must be an object.")
        unknown = set(self.orchestrator) - _ORCHESTRATOR_KEYS
        if unknown:
            raise RelayError("PROJECT_INVALID", f"Unknown orchestrator field(s): {sorted(unknown)}")
        if "enabled" in self.orchestrator and not isinstance(self.orchestrator["enabled"], bool):
            raise RelayError("PROJECT_INVALID", "orchestrator.enabled must be a boolean.")
        for key in ("worker", "model", "profile"):
            value = self.orchestrator.get(key)
            if value is not None and not isinstance(value, str):
                raise RelayError("PROJECT_INVALID", f"orchestrator.{key} must be a string.")
        for key in _ORCHESTRATOR_BUDGET_KEYS:
            if key not in self.orchestrator:
                continue
            value = self.orchestrator[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RelayError("PROJECT_INVALID", f"orchestrator.{key} must be a positive integer.")

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
        orchestrator = payload.get("orchestrator") or payload.get("orchestrator_json")
        if isinstance(orchestrator, str):
            orchestrator = json.loads(orchestrator)
        delivery = payload.get("delivery") or payload.get("delivery_json")
        if isinstance(delivery, str):
            delivery = json.loads(delivery)
        nodes = []
        for n in payload.get("nodes", []):
            node_type = str(n.get("type") or "task").strip().lower()
            nodes.append(
                ProjectNode(
                    node_id=str(n["node_id"]),
                    task_id=str(n.get("task_id") or "__relay_wait__"),
                    checkpoint=n.get("checkpoint"),
                    node_type=node_type,
                    wait=n.get("wait"),
                )
            )
        connections: list[ProjectConnection] = []
        aliases_by_node: dict[str, set[str]] = {}
        for c in payload.get("connections", []):
            from_output = str(c.get("from_output") or "").strip() or None
            to_input = str(c.get("to_input") or "").strip() or None
            from_role = str(c.get("from_role") or from_output or "")
            to_alias = str(c.get("to_alias") or "").strip()
            if not to_alias:
                used = aliases_by_node.setdefault(str(c.get("to_node") or ""), set())
                number = 1
                while f"A{number}" in used:
                    number += 1
                to_alias = f"A{number}"
            aliases_by_node.setdefault(str(c.get("to_node") or ""), set()).add(to_alias)
            connections.append(
                ProjectConnection(
                    from_node=str(c["from_node"]),
                    from_role=from_role,
                    to_node=str(c["to_node"]),
                    to_alias=to_alias,
                    from_output=from_output,
                    to_input=to_input,
                )
            )
        output_items = [
            {"node_id": str(o["node_id"]), "role": str(o["role"])} for o in payload.get("output_selection", [])
        ]
        return cls(
            nodes=nodes,
            connections=connections,
            output_selection=ProjectOutputSelection(items=output_items),
            failure_policy=str(payload.get("failure_policy", "stop")),
            notification_policy=notification_policy,
            orchestrator=orchestrator,
            delivery=delivery,
            description=payload.get("description"),
            project_summary=payload.get("project_summary"),
            name=payload.get("name"),
            project_id=payload.get("project_id"),
            version=int(payload.get("version", 1)),
        )


def collect_required_bindings(node_id: str, connections: Iterable[ProjectConnection]) -> list[ProjectConnection]:
    return [c for c in connections if c.to_node == node_id]
