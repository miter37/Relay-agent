# Phase 6b Comparison and Reproduction Design

**Date:** 2026-08-04
**Status:** Approved
**Source:** Phase 6 (6b) of docs/relay_product_direction_v1.0.md

## 1. Goal

Let operators compare two Runs or two Artifacts, see structured diffs, and partially re-execute from a chosen point while preserving prior outputs and lineage.

## 2. Scope

6b includes:

- Run-to-run comparison (Task Run vs Task Run, Project Run vs Project Run).
- Artifact-to-Artifact diff for text/JSON Artifacts with size/hash/role metadata.
- Partial re-execution of a Project Run from a selected node, preserving upstream successful Artifacts (refines Phase 4 retry-from-node).
- Comparison receipts recording what differed.
- Authenticated daemon APIs and machine-readable CLI commands.

6b excludes:

- Binary Artifact visual diff (images, PDFs) beyond hash/size metadata.
- Semantic equivalence scoring (that is 6c).
- Cross-Project comparison.

## 3. Run Comparison

`compare_runs(a_run_id, b_run_id)` produces:

- identity (task_id/project_id, versions, trigger).
- status, timing, worker, attempt counts.
- Artifact set diff: only-in-a, only-in-b, shared-with-different-hash.
- For text/JSON shared roles, a line-level or key-level structured diff.
- input lineage diff (which source Artifacts differed).

## 4. Artifact Diff

`diff_artifacts(a_uid, b_uid)` produces:

- metadata (role, mime_type, size, sha256 each).
- if both are decodable text/JSON: a structured diff (added/removed/changed lines or JSON paths).
- otherwise: hash-equality verdict and size delta only.

Diff size is capped (configurable, default 256 KiB) to bound context.

## 5. Partial Re-execution

Refines Phase 4 `retry from node` into a first-class partial re-execution:

- Re-run a single node with the same upstream Artifact snapshots (existing Phase 4 behavior, now exposed via CLI/API explicitly).
- Re-run from a node and cascade to descendants (existing Phase 4 behavior).
- The new Runs are ordinary Task/Project Runs linked to the original Project Run via a `reexecution_of` reference for traceability.
- Original Artifacts and lineage are never overwritten.

## 6. API

```text
GET /v1/runs/compare?a=<run_id>&b=<run_id>
GET /v1/artifacts/diff?a=<uid>&b=<uid>
POST /v1/project-runs/{id}/partial-reexecute   {"from_node": "...", "cascade": true}
```

Stable errors: COMPARE_INCOMPATIBLE (Task vs Project), DIFF_TOO_LARGE, PARTIAL_REEXECUTE_INVALID.

## 7. CLI

```text
relay compare runs <a-run-id> <b-run-id>
relay compare artifacts <a-uid> <b-uid>
relay project-run reexecute <project-run-id> --from-node analyze [--cascade] [--machine]
```

## 8. Completion Criteria

- Two Runs of the same Task/Project can be compared with a structured diff.
- Two text/JSON Artifacts can be diffed with line/key-level output.
- Partial re-execution from a node produces new Runs without overwriting originals.
- Comparison receipts are deterministic and bounded.
- CLI, daemon API, service, and DB primitives each have focused tests; full suite, Ruff, and compileall pass.
