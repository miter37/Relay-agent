# Phase 6a Human-in-the-loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Project checkpoint nodes that pause a Project Run for human review (approve/edit/reject), record human edits as lineage Artifacts, and deliver approved results to allow-listed external folders.

**Architecture:** A checkpoint is a per-node flag in the Project definition. The Project runtime transitions a completed checkpoint node to `awaiting_approval` instead of `completed`. A new ApprovalService handles approve/edit/reject and post-approval delivery. Human edits become producer=human Artifacts linked into lineage. Delivery uses the existing safe-path validation against an allow-list.

**Tech Stack:** Python 3.11+, SQLite, standard library, unittest, existing daemon/RPC, Ruff.

## Global Constraints

- Phase 6a covers daemon/API/CLI core only; approval GUI is deferred.
- The Project definition gains an optional `checkpoint` object per node; absent means no checkpoint.
- Delivery targets must be within configured allow-list roots; unknown kinds are rejected at Project create/update.
- Human edits are recorded as new Artifacts with producer=human; originals are never overwritten.
- awaiting_approval survives daemon restart (no re-dispatch of the checkpoint node).
- `relay-receipt.json`, `test_result.json`, `test_task.md` are never staged.
- Version string stays 1.1.0; work continues on `feat/phase0-domain-compat`.

---

### Task 1: DB primitives for approvals and delivery

**Files:** Modify `relay/db.py`; Create `tests/test_phase6a_db.py`

**Interfaces:**
- `Database.create_approval(row)`, `Database.get_approval(token)`, `Database.list_approvals(project_run_id)`, `Database.update_approval(token, **changes)`.
- `Database.create_delivery(row)`, `Database.list_deliveries(project_run_id)`.

- [ ] **Step 1: Write failing DB tests** — create_approval + get by token, list by project_run_id, update decision.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add `approvals` and `deliveries` tables** (additive migration v8→v9) with columns: approval_id, project_run_id, node_id, token, status(pending|approved|rejected), reviewer, reason, edited_artifact_uid, created_at, decided_at; delivery_id, project_run_id, approval_id, kind, target_path, artifact_uid, status, error, created_at.
- [ ] **Step 4: Add CRUD methods.**
- [ ] **Step 5: Run GREEN and commit** — `feat: add approval and delivery db primitives (phase 6a)`.

### Task 2: ProjectSpec checkpoint extension

**Files:** Modify `relay/projects/models.py`; Create `tests/test_phase6a_models.py`

- [ ] **Step 1: Write failing tests** — a node with `checkpoint.enabled=true` and `deliver_to` passes validation; an unknown deliver kind is rejected; a deliver path outside allow-list roots is rejected.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Extend ProjectNode** with optional `checkpoint` dict; validate `deliver_to` entries against a `delivery_allowlist_roots` config lookup.
- [ ] **Step 4: Run GREEN and commit** — `feat: add checkpoint node validation (phase 6a)`.

### Task 3: ApprovalService (approve/edit/reject + delivery)

**Files:** Create `relay/approvals/service.py`; Create `tests/test_phase6a_service.py`

**Interfaces:**
- `ApprovalService(db, engine, config)`.
- `create_pending_approval(project_run_id, node_id, draft_artifact_uids)` — records a pending approval and transitions the step to awaiting_approval.
- `approve(project_run_id, token, reviewer)` — step → completed, unblock descendants.
- `approve_with_edits(project_run_id, token, reviewer, file_path, role)` — record human Artifact, step → completed.
- `reject(project_run_id, token, reviewer, reason)` — step → failed, Project Run → failed.
- `deliver(project_run_id, token)` — copy approved Artifact to each deliver_to target, record deliveries.

- [ ] **Step 1: Write failing tests** — approve transitions, edit creates producer=human Artifact, reject fails run, deliver copies to allow-listed folder, non-allow-listed delivery rejected.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement ApprovalService.** Use existing safe_resolve + is_within for path validation.
- [ ] **Step 4: Run GREEN and commit** — `feat: add ApprovalService with delivery (phase 6a)`.

### Task 4: Project runtime checkpoint integration

**Files:** Modify `relay/projects/runtime.py`; Modify `tests/test_phase6a_service.py`

- [ ] **Step 1: Write failing test** — when a checkpoint node's Task Run completes, the step goes to awaiting_approval (not completed), and descendants stay blocked.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: In runtime reconciliation**, after marking a step completed, check if the node has checkpoint.enabled; if so, call ApprovalService.create_pending_approval and set step status to awaiting_approval.
- [ ] **Step 4: Run GREEN and commit** — `feat: pause project run at checkpoint nodes (phase 6a)`.

### Task 5: API + daemon routes + CLI

**Files:** Modify `relay/api.py`, `relay/daemon.py`, `relay/cli.py`; Create `tests/test_phase6a_api.py`, `tests/test_phase6a_cli.py`

- [ ] **Step 1: Write failing API/CLI tests.**
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add API functions** (`list_approvals`, `approve`, `reject`, `edit_approval`), daemon routes (`GET/POST /v1/project-runs/{id}/approvals/...`), and CLI (`relay approval list|show|approve|reject|edit`).
- [ ] **Step 4: Run GREEN and commit** — `feat: expose approval API and CLI (phase 6a)`.

### Task 6: Final verification

- [ ] **Step 1:** Full suite + Ruff + compileall + git diff --check.
- [ ] **Step 2:** Confirm user files untracked.
- [ ] **Step 3:** Commit and update log.md.

## Stop Gate

- Checkpoint node pauses Project Run in awaiting_approval.
- Approve/edit/reject produce correct lineage and state transitions.
- Human edits appear as producer=human Artifacts consumed by descendants.
- Delivery to allow-listed folder succeeds; non-allow-listed is rejected.
- Daemon restart preserves awaiting_approval.
- Full suite, Ruff, compileall pass.
