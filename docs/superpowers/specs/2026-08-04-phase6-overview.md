# Phase 6 Operations and Quality Hardening Overview

**Date:** 2026-08-04
**Status:** Approved
**Source:** docs/relay_product_direction_v1.0.md, Phase 6 (P3)

## 1. Scope

Phase 6 is the final roadmap phase. It is not a single subsystem; it is a bundle of independent operations-quality features grouped by product direction as P3 ("operational convenience and quality"). To keep each slice independently testable and reviewable, Phase 6 is split into five sub-projects, each with its own spec and plan:

- **6a Human-in-the-loop**: Human checkpoint awaiting_approval states and post-approval external folder/system delivery.
- **6b Comparison and reproduction**: Run-to-run comparison, Artifact diff, and partial re-execution.
- **6c Semantic search and quality scoring**: Embedding-backed semantic Artifact/Run search plus structured result quality scoring.
- **6d Operational observability**: Notification delivery (webhook), a Needs Attention inbox, and operational dashboards for Routine and Project.
- **6e Data lifecycle**: Export/import and backup plus receipt schema versioning.

Each sub-project is additive, GUI-deferred (CLI + daemon/API core only, matching the Schedule/Task/Project/Routine precedent), and reuses the Phase 0-5 Run/Artifact/Task/Project/Routine foundations unchanged.

## 2. Ordering

Recommended order, from highest core value to most self-contained:

1. 6a Human-in-the-loop (closes the Phase 4 deferral, most directly serves "results you can trust").
2. 6b Comparison and reproduction (builds on Artifact lineage from Phase 1).
3. 6d Operational observability (operationalization layer for Routines and Projects).
4. 6c Semantic search and quality scoring (most open-ended; benefits from stable data model first).
5. 6e Data lifecycle (export/import + receipt versioning; most self-contained, safe to do last).

The five sub-projects are independent; order can be changed without breaking dependencies.

## 3. Cross-cutting constraints

- All schema changes are additive and bump CURRENT_SCHEMA_VERSION once per sub-project.
- Each sub-project keeps existing Schedule, Task, Project, and Routine subsystems fully functional.
- input_policy/notification_policy fields added in Phase 5 are surfaced where relevant; enforcement lands in 6d.
- No GUI in any sub-project (deferred, consistent with all prior phases).
- relay-receipt.json, test_result.json, and test_task.md are never staged.
- Version string stays 1.1.0 unless a release cut is explicitly requested.
- Work continues on the feat/phase0-domain-compat branch.

## 4. Per-sub-project completion criteria

Each sub-project is complete when its own spec's completion criteria pass and the full test suite, Ruff, and compileall remain green.

## 5. Open questions

None at overview level. Each sub-project spec resolves its own open questions.
