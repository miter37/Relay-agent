"""Templated Project Run narration - no LLM call.

Relay already knows which node ran, how long it took, and what it produced, so a plain
run's story is fully told by these templates. The only thing that ever needs the LLM
tier is a closing explanation for a *failed* Run (``OrchestratorAgent.final_report``),
and only when the Run actually had an incident to explain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _duration_text(started_at: str | None, completed_at: str | None) -> str | None:
    start = _parse_iso(started_at)
    end = _parse_iso(completed_at)
    if not start or not end:
        return None
    seconds = max(0, int((end - start).total_seconds()))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


def narrate_run_started(spec: Any) -> str:
    node_count = len(spec.nodes)
    plural = "" if node_count == 1 else "s"
    return f"Starting Project Run: {node_count} node{plural} planned."


def narrate_step_dispatched(node_id: str, *, retry: bool = False) -> str:
    return f"{node_id} {'retrying' if retry else 'starting'}."


def narrate_step_completed(step: dict[str, Any]) -> str:
    node_id = step.get("node_id")
    duration = _duration_text(step.get("started_at"), step.get("completed_at"))
    return f"{node_id} completed in {duration}." if duration else f"{node_id} completed."


def narrate_run_completed(
    run: dict[str, Any], steps: list[dict[str, Any]], final_artifacts: list[dict[str, Any]]
) -> str:
    step_count = len(steps)
    artifact_count = len(final_artifacts)
    duration = _duration_text(run.get("started_at"), run.get("completed_at"))
    step_word = "step" if step_count == 1 else "steps"
    artifact_word = "artifact" if artifact_count == 1 else "artifacts"
    when = f" in {duration}" if duration else ""
    return f"Run completed{when}: {step_count} {step_word}, {artifact_count} final {artifact_word}."
