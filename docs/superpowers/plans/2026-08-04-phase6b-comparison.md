# Phase 6b Comparison and Reproduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Add Run-to-Run comparison, Artifact-to-Artifact diff, and first-class partial re-execution.

**Architecture:** A ComparisonService reads two Runs/Artifacts from the DB and produces a structured diff (metadata + text/JSON diff for decodable content). Partial re-execution refines Phase 4 retry-from-node and links new Runs to the original via a reexecution_of reference.

**Tech Stack:** Python 3.11+, SQLite, difflib, json, unittest, Ruff.

## Global Constraints

- Phase 6b is daemon/API/CLI core only; no GUI.
- Diffs are capped at 256 KiB to bound context.
- Partial re-execution never overwrites original Runs or Artifacts.
- Binary Artifacts are compared by hash/size only.
- `relay-receipt.json`, `test_result.json`, `test_task.md` never staged.
- Version stays 1.1.0; work on `feat/phase0-domain-compat`.

---

### Task 1: Run comparison service

**Files:** Create `relay/comparison/service.py`; Create `tests/test_phase6b_service.py`

**Interfaces:**
- `ComparisonService.compare_runs(db, a_run_id, b_run_id) -> dict` — identity, status, timing, worker, attempt counts, Artifact set diff (only-in-a, only-in-b, hash-differ), lineage diff.
- `ComparisonService.diff_artifacts(db, a_uid, b_uid) -> dict` — metadata, and for text/JSON a structured diff.

- [ ] **Step 1: Write failing tests** — two Task Runs of same Task compared; Artifact diff for JSON; incompatible (Task vs Project) rejected.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** using difflib for text, recursive key-diff for JSON, hash/size for binary.
- [ ] **Step 4: Run GREEN and commit** — `feat: add comparison and diff service (phase 6b)`.

### Task 2: Partial re-execution

**Files:** Modify `relay/projects/service.py`; Modify `tests/test_phase6b_service.py`

- [ ] **Step 1: Write failing test** — partial_reexecute from a node creates new Task Runs linked via reexecution_of; originals preserved.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add `partial_reexecute(project_run_id, from_node, cascade=True)` to ProjectService**, reusing retry logic but recording `reexecution_of` on new step runs.
- [ ] **Step 4: Run GREEN and commit** — `feat: add partial re-execution (phase 6b)`.

### Task 3: API + daemon routes + CLI

**Files:** Modify `relay/api.py`, `relay/daemon.py`, `relay/cli.py`; Create `tests/test_phase6b_api.py`, `tests/test_phase6b_cli.py`

- [ ] **Step 1: Write failing tests.**
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add routes** (`GET /v1/runs/compare`, `GET /v1/artifacts/diff`, `POST /v1/project-runs/{id}/partial-reexecute`) and CLI (`relay compare runs`, `relay compare artifacts`, `relay project-run reexecute`).
- [ ] **Step 4: Run GREEN and commit** — `feat: expose comparison API and CLI (phase 6b)`.

### Task 4: Final verification

- [ ] Full suite + Ruff + compileall + git diff --check + log.md.

## Stop Gate

- Two Runs of same Task/Project compared with structured diff.
- Two text/JSON Artifacts diffed with line/key output.
- Partial re-execution from a node produces new Runs without overwriting originals.
- Comparison receipts deterministic and bounded.
- Full suite, Ruff, compileall pass.
