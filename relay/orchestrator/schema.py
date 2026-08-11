"""Strict JSON contract for one Orchestrator repair decision.

The Orchestrator agent is asked to return exactly this shape, and nothing it returns is
trusted until it passes ``validate_decision_payload``: unknown actions, a ``node_id``
other than the one under repair, or extra fields are all rejected before anything is
applied. This is what keeps the LLM tier's authority equal to (never wider than) the
deterministic tier's - both ultimately produce the same ``RepairDecision`` shape.
"""

from __future__ import annotations

from typing import Any

from ..errors import RelayError
from .planner import RepairDecision

DECISION_ACTIONS = {"retry", "retry_with_worker", "rebind_connection", "rebind_output_role", "give_up"}

ORCHESTRATOR_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["action", "node_id", "reason"],
    "properties": {
        "action": {"type": "string", "enum": sorted(DECISION_ACTIONS)},
        "node_id": {"type": "string"},
        "reason": {"type": "string"},
        "note": {"type": "string"},
        "worker": {"type": ["string", "null"]},
        "addendum": {"type": ["string", "null"]},
        "connection_overrides": {"type": ["object", "null"]},
        "output_role": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}

_ALLOWED_KEYS = set(ORCHESTRATOR_DECISION_SCHEMA["properties"])

_REQUIRED_EXTRA_FIELD = {
    "retry_with_worker": "worker",
    "rebind_connection": "connection_overrides",
    "rebind_output_role": "output_role",
}


def validate_decision_payload(payload: Any, *, expected_node_id: str) -> RepairDecision:
    """Validate a decoded decision and convert it to a ``RepairDecision``.

    Raises ``RelayError("ORCHESTRATOR_DECISION_INVALID", ...)`` for any structural problem,
    including a ``node_id`` that does not match the failure under repair - node identity
    is fixed by the Supervisor, never something the agent chooses.
    """
    if not isinstance(payload, dict):
        raise RelayError("ORCHESTRATOR_DECISION_INVALID", "Decision must be a JSON object.")
    unknown = set(payload) - _ALLOWED_KEYS
    if unknown:
        raise RelayError("ORCHESTRATOR_DECISION_INVALID", f"Unknown decision field(s): {sorted(unknown)}")
    for key in ("action", "node_id", "reason"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise RelayError("ORCHESTRATOR_DECISION_INVALID", f"Decision field {key!r} must be a non-empty string.")
    action = payload["action"]
    if action not in DECISION_ACTIONS:
        raise RelayError("ORCHESTRATOR_DECISION_INVALID", f"Unknown decision action: {action!r}")
    if payload["node_id"] != expected_node_id:
        raise RelayError(
            "ORCHESTRATOR_DECISION_INVALID",
            f"Decision targets node {payload['node_id']!r} but the failure under repair is {expected_node_id!r}.",
        )

    worker = payload.get("worker")
    if worker is not None and not isinstance(worker, str):
        raise RelayError("ORCHESTRATOR_DECISION_INVALID", "Decision field 'worker' must be a string or null.")
    addendum = payload.get("addendum")
    if addendum is not None and not isinstance(addendum, str):
        raise RelayError("ORCHESTRATOR_DECISION_INVALID", "Decision field 'addendum' must be a string or null.")
    connection_overrides = payload.get("connection_overrides")
    if connection_overrides is not None:
        if not isinstance(connection_overrides, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in connection_overrides.items()
        ):
            raise RelayError(
                "ORCHESTRATOR_DECISION_INVALID",
                "Decision field 'connection_overrides' must be a string-to-string object.",
            )
    output_role = payload.get("output_role")
    if output_role is not None and not isinstance(output_role, str):
        raise RelayError("ORCHESTRATOR_DECISION_INVALID", "Decision field 'output_role' must be a string or null.")

    required_extra = _REQUIRED_EXTRA_FIELD.get(action)
    if required_extra and not payload.get(required_extra):
        raise RelayError("ORCHESTRATOR_DECISION_INVALID", f"Action {action!r} requires a non-empty {required_extra!r}.")

    return RepairDecision(
        strategy=action,
        node_id=payload["node_id"],
        reason=payload["reason"],
        worker=worker,
        addendum=addendum,
        connection_overrides=connection_overrides,
        output_role_override=output_role,
    )
