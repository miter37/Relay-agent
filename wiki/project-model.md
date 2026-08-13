# Project model

## Actors and entry points

- Humans and external agents submit work through `relay`, the authenticated daemon API, or the GUI.
- The daemon owns Job queues plus Schedule, Project, and Routine reconciliation loops.
- `RelayEngine` resolves Agent definitions, enforces readiness and service isolation, snapshots inputs, supervises workers, validates results, and records history.
- Internal Project/Routine execution records `caller=service`; `trigger_type` explains why a Run exists and `submitted_via` records its entry surface.

## Durable objects

| Product object | Persistence and rule |
|---|---|
| Task Run / Run | Existing `jobs` row; `run_id` aliases immutable `job_id` for compatibility. |
| Task | Versioned mutable definition in `tasks`; every Run keeps an immutable Task snapshot. |
| Attempt | `attempts` child row with the actual Worker and audit-bound execution metadata. |
| Artifact | Immutable UID, role, SHA-256, producer, and Relay-managed file. |
| Lineage | `artifact_lineage` records source Artifact, consumer Run, alias, and verified snapshot hashes. |
| Project | Versioned DAG definition containing Task nodes, Artifact-to-input connections, and final-output selection. |
| Project Run | Persistent state machine with immutable Project/Task snapshots and child Task Runs. |
| Routine | Timezone-aware recurring Task/Project target with overlap, missed-run, version, input, and notification policies. |
| Approval | Persistent checkpoint decision; human edits are new `producer=human` Artifacts linked to the original. |
| Review session / round | Optional final-result gate for a Task Run or Project node; tracks reviewer, guidelines, rerun budget, candidate evidence, decisions, and revision rounds. Candidate Artifacts are not published until confirmation. |
| Orchestrator | Optional per-Project config (`ProjectSpec.orchestrator`, absent by default) attached to a Project Run's snapshot; narrates and repairs that one Run within a budget. Never mutates the Project/Task definition. |
| Orchestrator event | `project_run_events` row (`note`/`decision`/`report`/`fallback`), seq-ordered per Run; the narration/decision timeline the GUI Orchestrator tab reads. |

## Execution flow

```text
caller → Task Run → Attempt(s) → Artifact(s)
                    ↑              ↓ immutable UID + hash snapshot
Project/Routine ────┘        next Task Run input manifest + lineage
```

Project connections are resolved strictly by `(source node, Artifact role)` and passed into child Task Runs as A1/A2-style Artifact inputs. The engine copies each input into Relay Home, verifies size and SHA-256, and records consumer lineage before a Worker receives it. Missing or ambiguous selected final Artifacts fail the Project Run.

Roles come from three places: Relay labels its own result file `result` and reserves that name; a Worker may label a file through `artifacts[].role` in its result JSON (lowercase, `^[a-z][a-z0-9_-]{0,31}$`); everything else defaults to `output`. Because both connection binding and final-output selection require exactly one match per `(node, role)`, a step that emits several files consumed separately must give each a distinct role.

Artifact inputs may resolve to a file under either Relay-managed storage root: `artifact_root` for produced files and `result_root` for the delivered result file. Both are integrity-checked by recorded size and SHA-256 before staging; paths outside those roots stay refused.

A step that fails before it produces a Task Run — missing Task snapshot, unresolvable inputs, a rejected request — blocks its descendants just as a failed Task Run does, so the Project Run always reaches a terminal state and stays retryable.

Each node's own configured Profile carries through dispatch: `JobRequest.profile` defaults to `None`, not a real profile string, precisely so a dispatch-built request without an explicit override doesn't clobber the Task snapshot's profile during the `run_task_from_snapshot` merge.

Routine ticks execute only due occurrences. Atomic occurrence claims prevent duplicate dispatch; pinned-version mismatches fail instead of silently running a newer Task/Project. Existing Schedules remain independent and continue producing ordinary linked Jobs.

All four overlap policies are honoured when a Run is still in flight: `skip` abandons the occurrence and advances, `queue` holds it without advancing and then dispatches one occurrence per tick so order is preserved, `cancel_previous` cancels the in-flight Task or Project Run before dispatching, and `allow_parallel` dispatches alongside it.

Checkpoint nodes pause in `awaiting_approval`; configured result-review nodes pause in `awaiting_review`. Review confirmation resumes descendants and publishes candidate Artifacts, feedback creates a new round and re-executes the node cascade, and rejection closes the candidate (Project rejection fails the run). Human review is unlimited; Orchestrator review uses the Project's existing worker/model/profile, bounded automatic reruns, strict evidence handling, and hands off to a human on uncertainty or budget exhaustion. Folder delivery is restricted to configured `allowed_delivery_roots` at both definition and delivery time.

FTS5 indexes are derived and rebuildable. Semantic search currently uses the pluggable embedding interface and explicitly falls back to FTS5 when no backend is configured. Quality attention covers Task and Project Runs. Export archives are deterministic, hash-manifested, redact notification secrets and local Artifact paths, and optionally round-trip Task Runs, Artifacts, and lineage.

The Task Run and Project Run catalogs default to their status/date tree. A segmented grouping control can instead organize Task Runs under their registered Task and Project Runs under their Project; children are execution dates, with repeated same-day runs disambiguated as `(2)`, `(3)`, and so on. Task/Project/Routine catalogs select on the current item so mouse and keyboard navigation behave alike. The Project Run detail opens with a full-width `Workspace` surface: stage summary, shared Artifact preview, and inline Review Panel. `Pipeline`, `Timeline`, and `Orchestrator` remain advanced evidence tabs, while `All Project Runs` restores the catalog without losing its filter/group/tree state. A global Reviews Inbox shows actionable Task/Project review sessions. Project Run Artifacts and review candidates are inspected from the Workspace; selecting a Pipeline node offers direct `Open Task` and `View result` actions, with the latter focusing that node's Artifact and Review in Workspace. Task Run and Project Run detail surfaces expose pending Review actions inline, while Reviews remains the cross-run inbox. Pipeline nodes show `awaiting_review` and retain review round/status history. Routine authoring provides guided schedule controls with JSON under Advanced, and the app shell provides Ctrl+K Quick find across loaded work.

When a Project attaches an Orchestrator (`docs/superpowers/plans/2026-08-10-project-orchestrator.md`), `ProjectRuntime` consults it automatically the moment a step fails or a final-output selection cannot match: a deterministic Tier 0 (`relay/orchestrator/planner.py`) resolves what it can — a plain retry, or a role/worker rebind where exactly one candidate exists — with no LLM call; only what Tier 0 leaves unresolved reaches a Tier 1 LLM call, dispatched as an ordinary Task Run (`submitted_via="orchestrator"`), and only while a per-node/per-run/LLM-call budget remains. Every decision, fallback, and narration note is appended to `project_run_events`; the `Orchestrator` tab renders that stream and the budget, and shows a disabled-state explanation when no Orchestrator is attached. A Project that never attaches one is unaffected byte-for-byte — no snapshot field, no event rows, no extra dispatch.
