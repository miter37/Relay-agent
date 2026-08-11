# Project Orchestrator Implementation Plan

> **Status (2026-08-10): Tasks 1-9 implemented, tested, and verified.** See `log.md` for the per-task commit record, `wiki/decisions.md` for the authority/budget model, and `wiki/project-model.md` for the durable-object and execution-flow additions. Two Task 9 scenarios (an LLM-authored addendum actually repairing a schema mismatch, and a real Tier-1 call) could not be reproduced end-to-end in this sandbox — no worker CLI is installed — and are instead covered at the mechanism level with a stubbed agent, per this plan's own Task 5 test design. A real-Qt manual visual pass of the new Orchestrator tab and Project editor section is still open (`memo.md`).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Project optionally attach an Orchestrator that carries the run to completion the way a human operator does today — narrating progress, diagnosing failures, repairing them within a bounded budget, and reporting the cause when it cannot.

**Core concept:** The Orchestrator is a delegated operator scoped to a single Project Run. Its powers are a strict subset of what a person can already do through the CLI and GUI, and nothing it does escapes that Run. It never owns the execution loop; `ProjectRuntime` keeps reconciling the DAG deterministically and consults the Orchestrator at defined events.

**Architecture:** Add a `relay/orchestrator/` package with a `RepairPlanner` (deterministic, no LLM) and an `OrchestratorAgent` (dispatched as an ordinary Task Run with `submitted_via="orchestrator"`). `ProjectRuntime` emits lifecycle events; a supervisor applies decisions through the existing `partial_reexecute` / `retry_project_run` paths plus a new run-scoped override overlay. All narration and decisions persist to a new `project_run_events` table that the GUI Orchestrator tab reads.

**Tech Stack:** Python 3.11+, SQLite (schema v14 → v15), existing `RelayEngine` worker dispatch and result-schema validation, PySide6, `unittest`, Ruff.

---

## Authority Model

| Lever a person has today | Orchestrator | Scope |
|---|---|---|
| Retry a failed step | allowed | run |
| Retry with a different worker | allowed | run |
| Edit Task instructions, then retry | allowed as **append-only addendum** | run |
| Fix a connection's `from_role`, then re-run | allowed, only to a role the producer actually emitted | run |
| Fix an `output_selection` role | allowed, only to a role that node actually emitted | run |
| Give up and report the cause | allowed | run |
| Change a Task's output schema | **forbidden** | — |
| Change which node delivers a final output | **forbidden** | — |
| Add, remove, or reorder nodes | **forbidden** | — |
| Edit the registered Project or Task definition | **forbidden** — records a promotion proposal for human approval | — |

Two invariants make the deliverable contract structural rather than a matter of trust:

1. Result validation always runs against the **original** Task output schema. An addendum cannot relax it.
2. `output_selection[].node_id` is immutable, so the identity of every final deliverable is fixed at run creation.

---

## Token Budget Model

Cost is incurred only where a human would otherwise have had to intervene.

| Situation | LLM calls |
|---|---|
| Run completes with no incident | **0** — narration and the final summary are templated |
| Failure resolved by the deterministic tier | **0** |
| Failure needing diagnosis | 1 per repair decision |
| Run that had any incident | +1 for the closing report |

Hard ceilings, enforced in code and not by prompt:

- `max_llm_calls_per_run` (default 8) — exceeding it forces a terminal failure report.
- Evidence packet caps: worker log tail ≤ 40 lines, `error_message` ≤ 2000 chars, artifact listings carry role and basename only — never file content.
- A bounded rolling `state_digest` (≤ 1500 chars) carries prior decisions into the next call so receipts are never resent.
- Decisions are strict JSON; no free-form reasoning is requested in the output.

---

## Repair Ladder

**Tier 0 — deterministic, no LLM.** Runs first on every failure.

| Signal | Repair |
|---|---|
| `DAEMON_RESTARTED`, transient `PROCESS_CRASHED` | plain retry |
| `PROJECT_ARTIFACT_MISSING` and the producing node emitted exactly one artifact | rebind `from_role` to it |
| `PROJECT_ARTIFACT_AMBIGUOUS` | not resolvable deterministically — escalate |
| Requested worker not installed and exactly one compatible worker is available | worker swap |

**Tier 1 — Orchestrator agent.** Only for what Tier 0 left unresolved, and only while budget remains. Receives the evidence packet, returns one decision.

**Tier 2 — terminal.** Budget exhausted, or the decision is `give_up`, or the same `(node_id, strategy)` pair has already been tried. Writes a failure report and stops. Never loops.

---

## Global Constraints

- Do not change Relay Home Artifacts, paths, manifests, or history semantics.
- An Orchestrator failure must never fail a Project Run: on any error, fall back to today's deterministic behavior and record the fallback.
- Absent orchestrator configuration means today's behavior, byte for byte. Existing Projects are unaffected by the upgrade.
- Overrides are run-scoped overlays. The registered Project and Task definitions are never mutated by the Orchestrator.
- Every override is attributed and reproducible from the receipt: who proposed it, why, and which attempt used it.
- Repair attempts are bounded per node and per run; the same `(node_id, strategy)` is never retried twice.
- Keep the daemon/GUI compatibility contract in lockstep when API responses change.
- Add regression tests for every lifecycle, budget, and override-boundary rule.

---

### Task 1: Schema and storage foundation

**Files:**
- Modify: `relay/db.py` (`CURRENT_SCHEMA_VERSION` 14 → 15, migration, accessors)
- Modify: `relay/projects/models.py`
- Create: `tests/test_orchestrator_storage.py`

**Interfaces:**
- New column `project_run_steps.step_overrides_json` holding `{worker_override?, instruction_addendum?, connection_overrides?}`.
- New table `project_run_events(event_id, project_run_id, node_id, seq, kind, actor, summary, detail_json, created_at)` where `kind` is one of `note`, `decision`, `repair`, `report`, `fallback`, and `actor` is `runtime` or `orchestrator`.
- New table `project_run_orchestrator_state(project_run_id, llm_calls_used, repair_attempts_used, state_digest, updated_at)`.
- `ProjectSpec.orchestrator: dict | None` with `{enabled, worker, profile, max_repair_attempts_per_node, max_repair_attempts_per_run, max_llm_calls_per_run}`; serialized in `_to_dict()` only when set, so existing snapshots stay byte-identical.

- [ ] **Step 1: Write failing tests** for the v14 → v15 migration preserving existing rows, `orchestrator=None` producing an unchanged `to_snapshot()`, orchestrator field validation rejecting unknown keys and non-positive budgets, and event append/read ordering by `seq`.
- [ ] **Step 2: Run the focused tests and confirm they fail.**
- [ ] **Step 3: Implement the migration** following the existing migration pattern in `relay/db.py`; back-fill `step_overrides_json` from any `worker_override` currently stashed in `resolved_connections_json`.
- [ ] **Step 4: Implement `ProjectSpec.orchestrator`** with validation in `ProjectSpec.validate()` raising `PROJECT_INVALID` on malformed configuration.
- [ ] **Step 5: Run the focused tests and confirm they pass.**

### Task 2: Retire the `resolved_connections_json` worker-override hack

**Files:**
- Modify: `relay/projects/runtime.py:_dispatch_step`
- Modify: `relay/projects/service.py:retry_project_run, partial_reexecute`
- Modify: `tests/test_project_runs_backend.py`

**Rationale:** `worker_override` is currently written into `resolved_connections_json`, the same field that otherwise holds resolved inputs, and the runtime tells them apart with an `isinstance(dict)` check. Layering more overrides on that field would compound the problem.

- [ ] **Step 1: Write failing tests** asserting `retry_project_run(worker=...)` writes to `step_overrides_json`, that `resolved_connections_json` holds only resolved inputs afterward, and that a legacy run carrying the old shape still dispatches with its override.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Move writes to `step_overrides_json`** and keep a read-time fallback for legacy rows.
- [ ] **Step 4: Run backend and Project Run GUI tests and confirm they pass.**

### Task 3: Run-scoped override overlay at dispatch

**Files:**
- Modify: `relay/projects/service.py:resolve_step_inputs`, `_finalize_completed` role matching
- Modify: `relay/projects/runtime.py:_dispatch_step`
- Create: `relay/orchestrator/overrides.py`
- Create: `tests/test_orchestrator_overrides.py`

**Interfaces:**
- `apply_instruction_addendum(instructions, addendum) -> str` appends a clearly delimited section; it never rewrites the original text.
- `effective_connections(spec, step_overrides) -> list[ProjectConnection]` applies `from_role` rebinds.
- `effective_output_selection(snapshot, run_overrides) -> list[dict]` applies role rebinds while `node_id` stays fixed.
- `validate_override(...)` rejects any rebind to a role the producing node did not actually emit, any `node_id` change, and any schema change.

- [ ] **Step 1: Write failing tests** for addendum append-only behavior, `from_role` rebind changing input resolution, `output_selection` role rebind fixing a finalize failure, rejection of a `node_id` change, rejection of a rebind to a nonexistent role, and result validation still using the original schema.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement the overlay helpers as pure functions** with no DB access.
- [ ] **Step 4: Wire the overlay into `_dispatch_step` and finalize-time role matching.**
- [ ] **Step 5: Run the tests and confirm they pass.**

### Task 4: Deterministic repair tier

**Files:**
- Create: `relay/orchestrator/planner.py`
- Create: `tests/test_orchestrator_planner.py`

**Interfaces:**
- `plan_repair(evidence) -> RepairDecision | None` returning `None` when nothing deterministic applies.
- `RepairDecision(strategy, node_id, reason, worker=None, addendum=None, connection_overrides=None)` where `strategy` is `retry`, `retry_with_worker`, `rebind_connection`, `rebind_output_role`, or `give_up`.
- `build_evidence(db, engine, project_run_id, node_id) -> Evidence` collecting the bounded packet described in the token budget model.

- [ ] **Step 1: Write failing tests** covering `DAEMON_RESTARTED` → retry, single-candidate role rebind, ambiguous roles → `None`, unavailable worker with one alternative → swap, unavailable worker with several alternatives → `None`, and evidence packet caps (log tail length, message truncation, no file content).
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement `build_evidence` and `plan_repair` as pure functions** over already-fetched data.
- [ ] **Step 4: Run the tests and confirm they pass.**

### Task 5: Orchestrator agent, decision contract, and budget enforcement

**Files:**
- Create: `relay/orchestrator/agent.py`
- Create: `relay/orchestrator/schema.py`
- Create: `relay/orchestrator/supervisor.py`
- Create: `tests/test_orchestrator_agent.py`

**Interfaces:**
- `ORCHESTRATOR_DECISION_SCHEMA` — strict JSON: `{action, node_id, reason, note, worker?, addendum?, connection_overrides?}` with `action` drawn from the same strategy set.
- `OrchestratorAgent.decide(evidence, state_digest) -> RepairDecision` dispatching one Task Run with `submitted_via="orchestrator"`, `caller="service"`, and the configured worker/profile.
- `Supervisor.on_step_failed(project_run_id, node_id)` runs Tier 0, then Tier 1 while budget remains, then Tier 2; applies the decision through `partial_reexecute` and the override overlay; appends a `decision` and a `repair` event.
- `Supervisor.consume_budget(...)` enforces per-node, per-run, and per-run-LLM ceilings and refuses a repeated `(node_id, strategy)` pair.

- [ ] **Step 1: Write failing tests** with a stubbed agent for: Tier 0 hit means zero agent calls; Tier 1 applies a valid decision; an out-of-authority decision (`node_id` change, schema change, unknown role) is rejected and recorded without being applied; per-node budget exhaustion escalates to terminal; a repeated `(node, strategy)` is refused; an agent timeout or malformed decision falls back to today's behavior and records a `fallback` event; the run still reaches a terminal state in every case.
- [ ] **Step 2: Write failing tests for budget accounting** across successive failures within one run, including the LLM-call ceiling.
- [ ] **Step 3: Run the tests and confirm they fail.**
- [ ] **Step 4: Implement the decision schema and agent dispatch,** validating the returned JSON before it is trusted.
- [ ] **Step 5: Implement the supervisor ladder and budget enforcement.**
- [ ] **Step 6: Run the tests and confirm they pass.**

### Task 6: Runtime event hooks and narration

**Files:**
- Modify: `relay/projects/runtime.py` (`_dispatch_step`, step completion, `_finalize_completed`, `_finalize_failed`)
- Create: `relay/orchestrator/narration.py`
- Create: `tests/test_orchestrator_narration.py`

**Interfaces:**
- `narrate_run_started(spec) -> str`, `narrate_step_completed(step, spec) -> str`, `narrate_run_completed(run, steps, final_artifacts) -> str` — all templated, no LLM.
- `OrchestratorAgent.final_report(state_digest, run_summary) -> str` — invoked only when the run recorded at least one incident.
- Hooks append events and are wrapped so that a hook failure can never abort reconciliation.

- [ ] **Step 1: Write failing tests** asserting a clean four-node run records start / per-step / completion notes with **zero** agent calls, that an incident run adds exactly one closing report call, that notes name the node and elapsed time, and that a raising hook does not disturb the run.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement templated narration and the guarded hooks.**
- [ ] **Step 4: Run the tests and confirm they pass.**

### Task 7: API and CLI surface

**Files:**
- Modify: `relay/api.py`
- Modify: `relay/cli.py`
- Modify: `relay/compatibility.py`
- Modify: `relay/projects/service.py:project_run_receipt`
- Create: `tests/test_orchestrator_api.py`

**Interfaces:**
- `GET /v1/project-runs/{id}/orchestrator` returns `{enabled, events, budget, promotion_proposals}`.
- The Project Run receipt gains `step_overrides` and `orchestrator_summary` per step.
- `relay project-run orchestrator <PRID>` prints the event stream; `relay project orchestrator set|show <PID>` manages configuration.
- Raise the API schema revision and the minimum GUI version together, per the existing compatibility contract.

- [ ] **Step 1: Write failing tests** for the endpoint shape, receipt additions, a run with no orchestrator returning `enabled: false` with an empty stream, and the compatibility revision bump.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement the endpoint, receipt fields, and CLI commands.**
- [ ] **Step 4: Run the tests and confirm they pass.**

### Task 8: GUI — Orchestrator tab and Project editor settings

**Files:**
- Modify: `relay/gui/project_runs.py` (`ProjectRunDetailView`, new `ProjectRunOrchestratorView`)
- Modify: `relay/gui/projects.py` (`ProjectEditorDialog`)
- Modify: `relay/gui/main_window.py` (response routing, polling)
- Modify: `tests/test_project_runs_gui.py`, `tests/fixtures/project_run_cases.py`

**Interfaces:**
- Tab order becomes `Pipeline | Artifacts | Timeline | Orchestrator`; the tab is present but shows a disabled-state explanation when no Orchestrator is attached.
- The event stream renders chronologically; a `decision` event renders as a card showing observation, decision, reason, and outcome, with the applied override in full.
- Budget consumption is shown as `repairs 2/4 · agent calls 3/8`.
- A promotion proposal renders with an **Apply to Project definition** action that opens the existing Project editor pre-filled; the Orchestrator never applies it.
- The verdict header carries an Orchestrator badge when one is attached.
- `ProjectEditorDialog` gains an Orchestrator section: enable, worker, profile, and the three budgets.

- [ ] **Step 1: Add fixtures** for an orchestrated run with a successful repair, one with an exhausted budget, one with a fallback event, and one with no orchestrator.
- [ ] **Step 2: Write failing widget tests** for tab presence and order, the disabled state, chronological ordering, decision-card contents, budget display, the promotion-proposal action not mutating anything by itself, and the editor round-tripping orchestrator settings.
- [ ] **Step 3: Run the tests and confirm they fail.**
- [ ] **Step 4: Implement `ProjectRunOrchestratorView`,** reusing the existing unavailable-state and sparse-refresh conventions so selection survives polling.
- [ ] **Step 5: Implement the editor section and wire response routing.**
- [ ] **Step 6: Run the GUI tests and confirm they pass.**

### Task 9: End-to-end verification and memory update

**Files:**
- Modify: `wiki/project-model.md`, `wiki/decisions.md`, `wiki/goals-and-scope.md`
- Modify: `memo.md`, `log.md`, `README.md`

- [ ] **Step 1: Reproduce a real schema-mismatch failure** and confirm the Orchestrator repairs it with an addendum and the run completes.
- [ ] **Step 2: Reproduce a real connection-role mismatch** and confirm Tier 0 repairs it with **zero** agent calls.
- [ ] **Step 3: Reproduce an unrepairable failure** (missing credential) and confirm it stops within budget and reports the cause.
- [ ] **Step 4: Confirm a Project with no Orchestrator behaves identically to today,** including snapshot bytes.
- [ ] **Step 5: Measure and record agent calls per scenario** in `log.md`; a clean run must be zero.
- [ ] **Step 6: Run the full suite** `python -m unittest discover -s tests` and Ruff on changed files.
- [ ] **Step 7: Verify the Orchestrator tab on real Qt** at 1280x720 and 1024x700 — never with `QT_QPA_PLATFORM=offscreen`, which has no fonts in this environment.
- [ ] **Step 8: Record the authority model and token model** in `wiki/decisions.md` and update `wiki/project-model.md`.
