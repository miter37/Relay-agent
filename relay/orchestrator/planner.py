"""Tier 0 of the Orchestrator repair ladder: deterministic, no LLM call.

Runs first on every step failure (docs/superpowers/plans/2026-08-10-project-orchestrator.md,
"Repair Ladder"). A clean run and most repairs never reach the LLM tier - a plain retry on
a transient failure and a role rebind against a producing node's actual output are both
decidable from data Relay already has, with no ambiguity to reason about.

``build_evidence`` gathers a bounded, already-fetched snapshot of one failure (or one
output-selection mismatch); ``plan_repair`` is a pure function over that snapshot so it is
trivially testable and reusable as-is by the LLM tier's evidence packet in Task 5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TRANSIENT_ERROR_CODES = {"DAEMON_RESTARTED", "PROCESS_CRASHED"}
_WORKER_UNAVAILABLE_CODES = {"WORKER_DISABLED", "WORKER_NOT_VERIFIED", "UNSUPPORTED_WORKER"}

_LOG_TAIL_MAX_LINES = 40
_LOG_LINE_MAX_CHARS = 500
_ERROR_MESSAGE_MAX_CHARS = 2000


@dataclass(slots=True)
class Evidence:
    """A bounded, already-fetched snapshot of one failure. No file content beyond the
    capped log tail is ever included - never Artifact bytes, never full instructions."""

    node_id: str
    error_code: str | None
    error_message: str | None
    log_tail: list[str] = field(default_factory=list)
    # Connection/output role-mismatch context, populated only when relevant.
    requested_role: str | None = None
    to_alias: str | None = None  # set only for a connection failure; identifies the input to rebind
    available_roles: list[str] = field(default_factory=list)  # roles actually emitted by the producing node
    # Worker-unavailability context.
    requested_worker: str | None = None
    available_workers: list[str] = field(default_factory=list)  # enabled alternatives, excluding the requested one


@dataclass(slots=True)
class RepairDecision:
    strategy: str  # retry | retry_with_worker | rebind_connection | rebind_output_role | give_up
    node_id: str
    reason: str
    worker: str | None = None
    addendum: str | None = None
    connection_overrides: dict[str, str] | None = None
    output_role_override: str | None = None


def _truncate(text: str | None, max_chars: int) -> str | None:
    if text is None:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _read_log_tail(path: str | None, max_lines: int = _LOG_TAIL_MAX_LINES) -> list[str]:
    if not path:
        return []
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = content.splitlines()[-max_lines:]
    return [_truncate(line, _LOG_LINE_MAX_CHARS) or "" for line in lines]


def build_evidence(db: Any, engine: Any, project_run_id: str, node_id: str) -> Evidence:
    """Build evidence for one failed step (the ``Supervisor.on_step_failed`` case)."""
    step = db.get_project_step(project_run_id, node_id)
    if not step:
        return Evidence(node_id=node_id, error_code=None, error_message="Step not found.")

    error_code = step.get("error_code")
    error_message = _truncate(step.get("error_message"), _ERROR_MESSAGE_MAX_CHARS)

    log_tail: list[str] = []
    last_task_run_id = step.get("active_task_run_id")
    if last_task_run_id:
        attempts = engine.db.attempts_for_job(last_task_run_id)
        if attempts:
            last_attempt = attempts[-1]
            log_tail = _read_log_tail(last_attempt.get("stderr_path") or last_attempt.get("stdout_path"))

    requested_role: str | None = None
    to_alias: str | None = None
    available_roles: list[str] = []
    requested_worker: str | None = None
    available_workers: list[str] = []

    if error_code in {"PROJECT_ARTIFACT_MISSING", "PROJECT_ARTIFACT_AMBIGUOUS"}:
        manifest = json.loads(step.get("input_manifest_json") or "[]")
        for entry in manifest:
            if entry.get("artifact_uid") is not None:
                continue  # already resolved or external; not the source of a connection failure
            source_node = entry.get("from_node")
            from_role = entry.get("from_role")
            source_step = db.get_project_step(project_run_id, source_node) if source_node else None
            if not source_step or not source_step.get("active_task_run_id"):
                continue
            artifacts = engine.db.artifacts_for_job(source_step["active_task_run_id"])
            roles = [a.get("role") for a in artifacts if a.get("role")]
            matches = [r for r in roles if r == from_role]
            if len(matches) != 1:
                requested_role = from_role
                to_alias = entry.get("to_alias")
                available_roles = sorted(set(roles))
                break

    elif error_code in _WORKER_UNAVAILABLE_CODES or (
        error_code == "ALL_WORKERS_FAILED" and error_message and "disabled" in error_message.lower()
    ):
        task_run = engine.db.get_job(last_task_run_id) if last_task_run_id else None
        requested_worker = task_run.get("requested_worker") if task_run else None
        try:
            all_workers = engine.agent_registry.list_agent_ids()
            available_workers = sorted(
                w
                for w in all_workers
                if w != requested_worker and w != "auto" and engine.agent_registry.get_worker_config(w).get("enabled")
            )
        except Exception:  # pragma: no cover - registry access is best-effort evidence
            available_workers = []

    return Evidence(
        node_id=node_id,
        error_code=error_code,
        error_message=error_message,
        log_tail=log_tail,
        requested_role=requested_role,
        to_alias=to_alias,
        available_roles=available_roles,
        requested_worker=requested_worker,
        available_workers=available_workers,
    )


def build_output_selection_evidence(
    db: Any, engine: Any, project_run_id: str, node_id: str, requested_role: str
) -> Evidence | None:
    """Build evidence for a final-output role mismatch (the run failed at finalize time,
    not at step dispatch - the producing node's own Task Run succeeded)."""
    step = db.get_project_step(project_run_id, node_id)
    if not step or not step.get("active_task_run_id"):
        return None
    artifacts = engine.db.artifacts_for_job(step["active_task_run_id"])
    available_roles = sorted({a.get("role") for a in artifacts if a.get("role")})
    return Evidence(
        node_id=node_id,
        error_code="PROJECT_ARTIFACT_MISSING",
        error_message=f"No final-output match for role {requested_role!r} on node {node_id!r}.",
        requested_role=requested_role,
        to_alias=None,
        available_roles=available_roles,
    )


def plan_repair(evidence: Evidence) -> RepairDecision | None:
    """Return a deterministic repair, or None when the failure needs the LLM tier (or is
    not repairable at all - the caller treats both the same: escalate or give up)."""
    if evidence.error_code in _TRANSIENT_ERROR_CODES:
        return RepairDecision(
            strategy="retry",
            node_id=evidence.node_id,
            reason=f"Transient failure ({evidence.error_code}); retrying as-is.",
        )

    if evidence.error_code == "PROJECT_ARTIFACT_MISSING" and evidence.to_alias is not None:
        if len(evidence.available_roles) == 1:
            role = evidence.available_roles[0]
            return RepairDecision(
                strategy="rebind_connection",
                node_id=evidence.node_id,
                reason=(
                    f"Upstream node emitted role {role!r}, not the declared "
                    f"{evidence.requested_role!r}; rebinding {evidence.to_alias}."
                ),
                connection_overrides={evidence.to_alias: role},
            )
        return None

    if evidence.error_code == "PROJECT_ARTIFACT_MISSING" and evidence.to_alias is None and evidence.available_roles:
        if len(evidence.available_roles) == 1:
            role = evidence.available_roles[0]
            return RepairDecision(
                strategy="rebind_output_role",
                node_id=evidence.node_id,
                reason=(
                    f"Node emitted role {role!r}, not the declared final-output role "
                    f"{evidence.requested_role!r}; rebinding the selection."
                ),
                output_role_override=role,
            )
        return None

    if evidence.error_code == "PROJECT_ARTIFACT_AMBIGUOUS":
        return None  # multiple candidates: not resolvable without judgment

    if evidence.requested_worker and evidence.error_code in _WORKER_UNAVAILABLE_CODES:
        if len(evidence.available_workers) == 1:
            return RepairDecision(
                strategy="retry_with_worker",
                node_id=evidence.node_id,
                reason=(
                    f"Requested worker {evidence.requested_worker!r} is unavailable; exactly one "
                    f"eligible alternative ({evidence.available_workers[0]!r}) is enabled."
                ),
                worker=evidence.available_workers[0],
            )
        return None

    return None
