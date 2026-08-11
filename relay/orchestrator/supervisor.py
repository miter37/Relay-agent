"""The Orchestrator's repair ladder and budget enforcement.

Tier 0 (``planner.plan_repair``, no LLM) runs first on every step failure. Only what it
cannot resolve reaches Tier 1 (the LLM ``OrchestratorAgent``), and only while budget
remains. Budget exhaustion, a repeated ``(node_id, strategy)`` pair, an out-of-authority
decision, or any error at any tier all fall back to Tier 2: a deterministic failure
report is recorded and the Supervisor returns ``None`` - it never loops, and a Supervisor
failure never blocks ``ProjectRuntime`` from reaching its own terminal state, since the
step simply stays failed exactly as it would without an Orchestrator attached.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from .planner import Evidence, RepairDecision, build_evidence, build_output_selection_evidence, plan_repair

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "max_repair_attempts_per_node": 2,
    "max_repair_attempts_per_run": 6,
    "max_llm_calls_per_run": 8,
}


class _Agent(Protocol):
    def decide(self, evidence: Evidence, state_digest: str) -> RepairDecision: ...


class Supervisor:
    def __init__(self, db: Any, engine: Any, *, agent_factory: Any = None):
        self.db = db
        self.engine = engine
        # Injected for testing; production default builds a real OrchestratorAgent.
        self._agent_factory = agent_factory or self._default_agent_factory

    @staticmethod
    def _default_agent_factory(engine: Any, config: dict[str, Any]) -> _Agent:
        from .agent import OrchestratorAgent

        return OrchestratorAgent(
            engine, worker=config.get("worker"), model=config.get("model"), profile=config.get("profile")
        )

    @staticmethod
    def config_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """Pure lookup for callers (``ProjectRuntime``) that already have the Run's
        parsed snapshot loaded, so hooks that fire on every reconcile tick don't pay
        for a second ``project_runs`` fetch when no Orchestrator is even attached."""
        config = snapshot.get("project_definition", {}).get("orchestrator")
        if not config or not config.get("enabled"):
            return None
        return {**_DEFAULTS, **config}

    def build_agent(self, config: dict[str, Any]) -> _Agent:
        return self._agent_factory(self.engine, config)

    def orchestrator_config(self, project_run_id: str) -> dict[str, Any] | None:
        run = self.db.get_project_run(project_run_id)
        if not run:
            return None
        return self.config_from_snapshot(json.loads(run["project_snapshot_json"]))

    # --- step-failure entry point -----------------------------------------------

    def on_step_failed(self, project_run_id: str, node_id: str) -> RepairDecision | None:
        config = self.orchestrator_config(project_run_id)
        if not config:
            return None  # No Orchestrator attached: today's behavior applies unchanged.

        evidence = build_evidence(self.db, self.engine, project_run_id, node_id)
        return self._resolve(project_run_id, node_id, evidence, config)

    def on_output_selection_failed(
        self, project_run_id: str, node_id: str, requested_role: str
    ) -> RepairDecision | None:
        config = self.orchestrator_config(project_run_id)
        if not config:
            return None
        evidence = build_output_selection_evidence(self.db, self.engine, project_run_id, node_id, requested_role)
        if evidence is None:
            return None
        return self._resolve(project_run_id, node_id, evidence, config)

    # --- ladder --------------------------------------------------------------

    def _resolve(
        self, project_run_id: str, node_id: str, evidence: Evidence, config: dict[str, Any]
    ) -> RepairDecision | None:
        decision = plan_repair(evidence)
        actor = "runtime"

        if decision is None:
            if self.llm_calls_used(project_run_id) >= config["max_llm_calls_per_run"]:
                self._record_event(
                    project_run_id,
                    node_id,
                    "fallback",
                    "runtime",
                    "LLM call budget exhausted for this Run; reporting the failure as-is.",
                )
                return None
            try:
                agent = self.build_agent(config)
                state_digest = self.state_digest(project_run_id)
                decision = agent.decide(evidence, state_digest)
                actor = "orchestrator"
                self._consume_llm_call(project_run_id)
            except Exception as exc:  # noqa: BLE001 - any agent failure must fall back, not propagate
                logger.warning("orchestrator agent failed for %s/%s: %s", project_run_id, node_id, exc)
                self._record_event(
                    project_run_id,
                    node_id,
                    "fallback",
                    "orchestrator",
                    f"Orchestrator call failed ({exc}); falling back to a deterministic failure report.",
                )
                return None

        if decision.strategy == "give_up":
            self._record_event(project_run_id, node_id, "report", actor, decision.reason)
            return None

        authority_error = self._authority_violation(decision, evidence)
        if authority_error:
            self._record_event(
                project_run_id,
                node_id,
                "report",
                actor,
                f"Decision rejected (out of authority): {authority_error}",
                detail={"strategy": decision.strategy, "rejected_reason": authority_error},
            )
            return None

        if self.repair_attempts_used(project_run_id, node_id) >= config["max_repair_attempts_per_node"]:
            self._record_event(
                project_run_id,
                node_id,
                "report",
                actor,
                f"Per-node repair budget exhausted for {node_id!r}; reporting the failure as-is.",
            )
            return None
        if self.repair_attempts_used(project_run_id, node_id=None) >= config["max_repair_attempts_per_run"]:
            self._record_event(
                project_run_id,
                node_id,
                "report",
                actor,
                "Per-run repair budget exhausted; reporting the failure as-is.",
            )
            return None

        if decision.strategy in self._strategies_used(project_run_id, node_id):
            self._record_event(
                project_run_id,
                node_id,
                "report",
                actor,
                f"Strategy {decision.strategy!r} was already attempted on {node_id!r}; refusing to repeat it.",
            )
            return None

        self._apply_decision(project_run_id, node_id, decision)
        self._record_event(
            project_run_id,
            node_id,
            "decision",
            actor,
            decision.reason,
            detail={
                "strategy": decision.strategy,
                "worker": decision.worker,
                "addendum": decision.addendum,
                "connection_overrides": decision.connection_overrides,
                "output_role_override": decision.output_role_override,
            },
        )
        return decision

    # --- authority boundary ---------------------------------------------------

    @staticmethod
    def _authority_violation(decision: RepairDecision, evidence: Evidence) -> str | None:
        """Reject a decision that claims more than the evidence actually supports.

        This is defense in depth: rebinds are already enforced structurally at apply
        time (Task 3 - a role nothing produced simply fails to match), but rejecting
        here means the bad decision never gets applied at all, and is recorded as
        rejected rather than as a failed retry.
        """
        if decision.strategy == "rebind_connection":
            if not decision.connection_overrides:
                return "rebind_connection carries no connection_overrides."
            for alias, role in decision.connection_overrides.items():
                if evidence.to_alias and alias == evidence.to_alias and evidence.available_roles:
                    if role not in evidence.available_roles:
                        return f"role {role!r} was not among the roles actually available: {evidence.available_roles}"
        elif decision.strategy == "rebind_output_role":
            if evidence.available_roles and decision.output_role_override not in evidence.available_roles:
                return (
                    f"role {decision.output_role_override!r} was not among the roles actually "
                    f"available: {evidence.available_roles}"
                )
        elif decision.strategy == "retry_with_worker":
            if evidence.available_workers and decision.worker not in evidence.available_workers:
                return f"worker {decision.worker!r} was not among the available workers: {evidence.available_workers}"
        return None

    # --- applying a decision ---------------------------------------------------

    def _apply_decision(self, project_run_id: str, node_id: str, decision: RepairDecision) -> None:
        overrides: dict[str, Any] = {}
        if decision.worker:
            overrides["worker_override"] = decision.worker
        if decision.addendum:
            overrides["instruction_addendum"] = decision.addendum
        if decision.connection_overrides:
            overrides["connection_overrides"] = decision.connection_overrides
        if decision.output_role_override:
            overrides["output_role_override"] = decision.output_role_override

        payload: dict[str, Any] = {
            "status": "pending",
            "active_task_run_id": None,
            "error_code": None,
            "error_message": None,
        }
        if overrides:
            payload["step_overrides_json"] = json.dumps(overrides)
        self.db.update_project_step(project_run_id, node_id, **payload)

        # Rescue blocked descendants exactly as a human retry does.
        for step in self.db.list_project_steps(project_run_id):
            if step["node_id"] != node_id and step["status"] == "blocked":
                self.db.update_project_step(
                    project_run_id, step["node_id"], status="pending", error_code=None, error_message=None
                )

        self.db.update_project_run(project_run_id, status="running", completed_at=None, started_at=None)

    # --- budget bookkeeping (project_run_orchestrator_state + event history) ---

    def _state_row(self, project_run_id: str) -> dict[str, Any]:
        return self.db.get_orchestrator_state(project_run_id) or {
            "llm_calls_used": 0,
            "repair_attempts_used": 0,
            "state_digest": None,
        }

    def llm_calls_used(self, project_run_id: str) -> int:
        return int(self._state_row(project_run_id).get("llm_calls_used") or 0)

    def _consume_llm_call(self, project_run_id: str) -> None:
        used = self.llm_calls_used(project_run_id) + 1
        self.db.upsert_orchestrator_state(project_run_id, llm_calls_used=used)

    def _repair_events(self, project_run_id: str, node_id: str | None) -> list[dict[str, Any]]:
        events = self.db.list_project_run_events(project_run_id)
        return [e for e in events if e.get("kind") == "decision" and (node_id is None or e.get("node_id") == node_id)]

    def repair_attempts_used(self, project_run_id: str, node_id: str | None) -> int:
        return len(self._repair_events(project_run_id, node_id))

    def _strategies_used(self, project_run_id: str, node_id: str) -> set[str]:
        strategies: set[str] = set()
        for event in self._repair_events(project_run_id, node_id):
            try:
                detail = json.loads(event.get("detail_json") or "{}")
            except (TypeError, ValueError):
                continue
            strategy = detail.get("strategy") if isinstance(detail, dict) else None
            if strategy:
                strategies.add(strategy)
        return strategies

    def state_digest(self, project_run_id: str) -> str:
        """A bounded summary of prior decisions in this Run, carried into the next LLM
        call instead of resending the full event history."""
        events = self.db.list_project_run_events(project_run_id)
        lines = [
            f"- {e['node_id'] or 'run'}: {e['summary']}" for e in events if e.get("kind") in {"decision", "report"}
        ]
        digest = "\n".join(lines[-10:])
        if len(digest) > 1500:
            digest = digest[-1500:]
        self.db.upsert_orchestrator_state(project_run_id, state_digest=digest)
        return digest

    def _record_event(
        self,
        project_run_id: str,
        node_id: str | None,
        kind: str,
        actor: str,
        summary: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.db.append_project_run_event(
            project_run_id, node_id=node_id, kind=kind, actor=actor, summary=summary, detail=detail
        )
