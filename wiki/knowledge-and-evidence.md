# Knowledge and evidence

## Verified facts

- Relay version remains 1.1.0; daemon API schema revision remains 5 with minimum GUI 1.1.0. Source: current code and tests.
- Current SQLite schema is v17. Legacy databases receive additive migrations through Task/Project summaries, Projects, Routines, approvals, notifications, Artifact producer metadata, and review sessions/rounds. Source: `relay/db.py`, migration fixtures, and `tests/test_reviews.py`.
- Project Artifact connections become real child Run input manifests and lineage rows, not display-only metadata. Source: Project runtime acceptance regression.
- Project and Routine child Runs use `caller=service` and therefore enforce service-isolation acknowledgement. Source: engine/runtime tests.
- Routine ticks do not dispatch future occurrences; pinned mismatches fail with `ROUTINE_VERSION_PIN_INVALID`. Source: Routine runtime regressions.
- Pending checkpoint creation is atomic per Project step; human edits are producer=human Artifacts with original-draft lineage. Source: approval concurrency and edit tests.
- Checkpoint delivery paths are validated against `allowed_delivery_roots` when Projects are created/updated and again immediately before copy-out. Source: Phase 6a path-boundary tests.
- Optional result review gates are persisted per Task Run and Project node. Candidate outputs and Working-folder deltas remain unpublished until confirmation; human feedback creates a new review round and rerun, while Orchestrator review fails closed to human handoff. Source: `relay/reviews/service.py`, `relay/engine.py`, `tests/test_reviews.py`.
- Routine failure policies invoke webhook notifications, retry up to the configured attempt count, and log every attempt. Source: Phase 6d service tests.
- Project failure notification policies and Project Runs are included in quality/attention views. Source: Phase 6c/6d regression tests.
- Export manifests carry per-entry SHA-256 values, redact webhook secrets, and optional import restores Task Runs and Artifact bytes under constrained paths. Source: Phase 6e round-trip/security tests.
- Import rename conflicts rewrite Task IDs inside Project definitions and Project IDs inside Routine targets. Source: lifecycle reference-integrity regression.
- Task and Task Run catalog endpoints provide bounded metadata, opaque cursor pagination, exact status/Task/date filters, and no raw prompt or Result content. Source: `relay/api.py`, daemon route tests, and `tests/test_catalog.py`.
- New Task Run receipts use schema v3 summary fields; the full local suite currently passes 819 tests with one skip under `D:\Python314\python.exe`. Source: `relay/receipts.py`, `relay/engine.py`, and full unittest discovery.
- The Hermes Relay skill, README, and manual now direct Agents through Catalog-first Task/Task Run selection and immutable Artifact UID reuse with Lineage verification. Source: `skills/hermes-relay/SKILL.md`, documentation examples, and catalog E2E tests.
- A controlled mission suite validated 5 standalone Tasks, 2 Artifact continuation chains, 3 Project shapes, 10 cataloged Task Runs, failed-Run reporting, Project completion, and `ARTIFACT_CHANGED` tamper rejection. External provider execution was intentionally not invoked. Source: `docs/Relay_Agent_Skill_Mission_Validation_Report_v1.0.md`.
- Bundled mock Worker E2E now executes five registered Tasks, two Artifact chains, three Project shapes, CLI smoke, receipt delivery, and Project Catalog reads without direct DB status mutation. Source: `tests/test_agent_mission_e2e.py`.
- The 2026-08-04 orchestration validation completed 11 registered Tasks, 12 Task Runs, three Projects, a standalone Artifact chain, a parallel join, cross-Project Artifact snapshot input with lineage, and failure recovery. Its initial search gap was fixed by terminal Run/Artifact indexing and stale-index backfill; success Run, failed Run, Artifact, and reopen-backfill searches now pass. Source: `docs/Relay_Agent_Orchestration_Scenario_Validation_Report_v1.0.md`, `relay/engine.py`, `relay/db.py`, and `tests/test_agent_orchestration_scenarios.py`.
- The final verification also runs Ruff, compileall, diff check, and the full unittest suite. CI status is not inferred from local results.
- On 2026-08-04, real installed Worker health was restored and verified: Codex 0.144.3 required a Codex-only schema where every top-level property is required; Claude 2.1.221 required CLI login and now reports `AUTH_REQUIRED` when unauthenticated; Antigravity 1.1.10 passed deep doctor in an isolated full-access temporary Home. Source: `relay/adapters/codex.py`, `relay/doctor.py`, and `docs/Relay_Agent_Worker_Health_Validation_Report_v1.0.md`.
- On 2026-08-04, real Codex and Claude complex orchestration runs completed through parallel analysis, Project synthesis, Artifact handoff to a follow-up Task, lineage, and search; real Antigravity S1–S6 had already passed. ProjectRuntime now treats successful `PARTIAL` Task Runs as terminal for dependency progression and records a non-blocking warning, and request files show the actual `input/<snapshot filename>` path for Artifact inputs. Source: `relay/projects/runtime.py`, `relay/request_builder.py`, `docs/Relay_Agent_Live_Worker_Scenario_Validation_Report_v1.0.md`, and focused regressions.

## Current uncertainty and deferred scope

- GUI management surfaces for Phase 3–6 domain objects are deferred; the CLI and daemon API are the complete interfaces today.
- No concrete embedding provider ships with Relay; semantic queries use the documented FTS5 fallback unless an operator supplies one.
- Export/import currently focuses Run restoration on Task Runs and their Artifacts/lineage; full Project/Routine operational-history restoration remains follow-up work.
- The Phase 0–6 branch `feat/phase0-domain-compat` is pushed to origin at the reviewed commit; no main merge, PR, or release cut is implied.
