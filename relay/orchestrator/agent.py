"""Tier 1 of the Orchestrator repair ladder: one LLM call, only when Tier 0 can't decide.

The agent is dispatched as an ordinary Task Run (``submitted_via="orchestrator"``), so
worker selection, timeouts, and history all come for free and the call is auditable like
any other Run - the Orchestrator never talks to a worker directly. Nothing it returns is
trusted until ``schema.validate_decision_payload`` accepts it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import RelayError
from ..models import JobRequest
from .planner import Evidence, RepairDecision
from .schema import ORCHESTRATOR_DECISION_SCHEMA, validate_decision_payload

_PROMPT_TEMPLATE = """You are the Orchestrator repairing one failed step of a Relay Project Run.

Node under repair: {node_id}
Error code: {error_code}
Error message: {error_message}
Requested role: {requested_role}
Available roles actually produced by the upstream node: {available_roles}
Requested worker: {requested_worker}
Available (enabled) alternative workers: {available_workers}
Recent log tail:
{log_tail}

Prior decisions already made in this Run:
{state_digest}

Your authority is limited to what a human operator could already do through the CLI/GUI
for this one Run: retry, swap to one of the available workers listed above, add an
instruction addendum for this attempt only, or rebind a connection/output-role to one of
the available roles listed above. You cannot change a Task's output schema, add or remove
nodes, or change which node delivers a final output. Never invent a role or worker not
listed above; if nothing here is repairable, respond with action "give_up".

Respond with ONLY a single JSON object matching this schema - no prose, no markdown fences:
{schema}

"node_id" MUST be exactly "{node_id}".
"""


def render_prompt(evidence: Evidence, state_digest: str) -> str:
    return _PROMPT_TEMPLATE.format(
        node_id=evidence.node_id,
        error_code=evidence.error_code or "(none)",
        error_message=evidence.error_message or "(none)",
        requested_role=evidence.requested_role or "(n/a)",
        available_roles=", ".join(evidence.available_roles) or "(none)",
        requested_worker=evidence.requested_worker or "(n/a)",
        available_workers=", ".join(evidence.available_workers) or "(none)",
        log_tail="\n".join(evidence.log_tail) or "(no log captured)",
        state_digest=state_digest or "(none)",
        schema=json.dumps(ORCHESTRATOR_DECISION_SCHEMA),
    )


class OrchestratorAgent:
    def __init__(
        self, engine: Any, *, worker: str | None = None, model: str | None = None, profile: str | None = None
    ):
        self.engine = engine
        self.worker = worker or "auto"
        self.model = model
        self.profile = profile

    def decide(self, evidence: Evidence, state_digest: str) -> RepairDecision:
        prompt = render_prompt(evidence, state_digest)
        request = JobRequest(
            task=prompt,
            caller="service",
            worker=self.worker,
            model=self.model,
            profile=self.profile,
            result_format="json",
        )
        receipt = self.engine.run(request, submitted_via="orchestrator")
        payload = self._extract_decision_payload(receipt)
        return validate_decision_payload(payload, expected_node_id=evidence.node_id)

    def final_report(self, state_digest: str, run_summary: str) -> str:
        """Ask the agent for a short closing explanation of a failed Run.

        Templated narration (``narration.narrate_run_completed``) already covers a
        successful Run in full, so this is only ever called for a failure - and only
        when the Run recorded at least one incident to explain.
        """
        prompt = (
            "Write a short (2-3 sentence) closing report explaining why this Relay "
            "Project Run failed, for the person who will read it. Plain text, no "
            "markdown. Base it only on the facts below; never invent a cause.\n\n"
            f"Run summary:\n{run_summary}\n\nPrior decisions in this Run:\n{state_digest or '(none)'}\n"
        )
        request = JobRequest(
            task=prompt,
            caller="service",
            worker=self.worker,
            model=self.model,
            profile=self.profile,
            result_format="text",
        )
        receipt = self.engine.run(request, submitted_via="orchestrator")
        if not receipt.get("ok", receipt.get("status") in {"completed", "partial"}):
            raise RelayError(
                "ORCHESTRATOR_REPORT_FAILED",
                f"Orchestrator closing report Task Run did not complete: "
                f"{receipt.get('error_code') or receipt.get('status')}",
            )
        result_path = receipt.get("result_path")
        if not result_path:
            raise RelayError("ORCHESTRATOR_REPORT_FAILED", "Orchestrator closing report produced no result file.")
        content = Path(result_path).read_text(encoding="utf-8").strip()
        try:
            decoded = json.loads(content)
        except ValueError:
            return content
        if isinstance(decoded, dict):
            for key in ("content", "summary", "text"):
                value = decoded.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return content

    @staticmethod
    def _extract_decision_payload(receipt: dict[str, Any]) -> Any:
        if not receipt.get("ok", receipt.get("status") in {"completed", "partial"}):
            raise RelayError(
                "ORCHESTRATOR_DECISION_INVALID",
                f"Orchestrator Task Run did not complete: {receipt.get('error_code') or receipt.get('status')}",
            )
        result_path = receipt.get("result_path")
        if not result_path:
            raise RelayError("ORCHESTRATOR_DECISION_INVALID", "Orchestrator Task Run produced no result file.")
        try:
            decoded = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RelayError("ORCHESTRATOR_DECISION_INVALID", f"Orchestrator result was not valid JSON: {exc}") from exc
        if isinstance(decoded, dict) and isinstance(decoded.get("content"), dict):
            return decoded["content"]
        return decoded
