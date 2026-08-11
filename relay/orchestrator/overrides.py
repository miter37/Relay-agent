"""Run-scoped override overlay applied at Project Run dispatch and finalize time.

These helpers implement the Orchestrator's authority boundary from
docs/superpowers/plans/2026-08-10-project-orchestrator.md: an Orchestrator (or a human,
through the same mechanism) may append to a Task's instructions for one attempt and may
correct which role a connection or a final output binds to, but it can never change a
Task's output schema, a node's identity, or which node delivers a final output. The
registered Project and Task definitions are never mutated by any of this - everything
here reads ``step_overrides_json`` and produces a new value for one dispatch, nothing is
written back to the Project/Task tables.

Role rebinds are not separately validated here: both the connection resolver
(``ProjectService.resolve_step_inputs``) and the finalize matcher
(``ProjectRuntime._finalize_completed``) already require exactly one Artifact with the
requested role on the producing node's Task Run, so a rebind to a role nothing emitted
fails with the same ``PROJECT_ARTIFACT_MISSING``/``PROJECT_ARTIFACT_AMBIGUOUS`` errors a
human's own mistyped role would - the override can only ever point at real output.
"""

from __future__ import annotations

from typing import Any

_ADDENDUM_HEADER = "\n\n--- Orchestrator repair note (this attempt only) ---\n"


def apply_instruction_addendum(instructions: str, addendum: str | None) -> str:
    """Append a run-scoped repair note; the original instructions are never rewritten."""
    if not addendum or not addendum.strip():
        return instructions
    return f"{instructions}{_ADDENDUM_HEADER}{addendum.strip()}\n"


def effective_manifest_entries(
    manifest: list[dict[str, Any]], connection_overrides: dict[str, str] | None
) -> list[dict[str, Any]]:
    """Apply ``from_role`` rebinds to a step's connection-sourced input manifest entries.

    ``connection_overrides`` maps ``to_alias`` -> corrected ``from_role``. Only entries
    still awaiting connection resolution (``artifact_uid`` is ``None``) are eligible;
    external inputs and already-resolved entries pass through unchanged.
    """
    if not connection_overrides:
        return manifest
    result: list[dict[str, Any]] = []
    for entry in manifest:
        alias = entry.get("to_alias")
        if entry.get("artifact_uid") is None and alias in connection_overrides:
            entry = dict(entry)
            entry["from_role"] = connection_overrides[alias]
        result.append(entry)
    return result


def effective_output_role(default_role: str, override_role: str | None) -> str:
    """Apply a final-output role rebind recorded on the target node's own step overrides.

    Callers read ``output_role_override`` from the specific node's ``step_overrides_json``
    before calling this, so node identity is already fixed by which step was read; this
    function only ever changes the role used to find the matching Artifact - never which
    node delivers this final output.
    """
    return override_role if override_role else default_role


def parse_step_overrides(step_overrides_json: str | None) -> dict[str, Any]:
    """Decode a step's override overlay, tolerating missing/malformed storage."""
    if not step_overrides_json:
        return {}
    import json

    try:
        decoded = json.loads(step_overrides_json)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
