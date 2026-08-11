# Phase 6d Operational Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Add webhook notification delivery, a Needs Attention inbox, and Routine/Project operational dashboards.

**Architecture:** A NotificationService enforces Phase 5's stored notification policies and delivers to webhook sinks (HMAC-signed, allow-listed, retry-logged). An AttentionService aggregates failed/low-quality/awaiting-approval items. Dashboards compute aggregates on read from existing tables.

**Tech Stack:** Python 3.11+, urllib, hmac, hashlib, SQLite, unittest, Ruff.

## Global Constraints

- Phase 6d is daemon/API/CLI core only; no GUI.
- Webhook URLs must match an allow-list (default localhost only).
- Notification delivery is best-effort with retry; results are logged.
- Dashboards are computed on read; no denormalized counters.
- `relay-receipt.json`, `test_result.json`, `test_task.md` never staged.
- Version stays 1.1.0; work on `feat/phase0-domain-compat`.

---

### Task 1: Notification event DB primitives

**Files:** Modify `relay/db.py`; Create `tests/test_phase6d_db.py`

**Interfaces:**
- `Database.create_notification_event(row)`, `Database.list_notification_events(*, routine_id=None, limit=100)`.

- [ ] **Step 1: Write failing tests.**
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add `notification_events` table** (additive migration) with event_id, routine_id, project_run_id, trigger, sink_url, status, status_code, attempt, error, payload_hash, created_at.
- [ ] **Step 4: Add CRUD.**
- [ ] **Step 5: Run GREEN and commit** — `feat: add notification event db primitives (phase 6d)`.

### Task 2: Webhook sink + NotificationService

**Files:** Create `relay/notifications/sink.py`, `relay/notifications/service.py`; Create `tests/test_phase6d_service.py`

**Interfaces:**
- `WebhookSink.deliver(url, secret, payload) -> dict` — HMAC-SHA256 sign, POST, record status_code, retry once on transient failure.
- `NotificationService.notify(db, config, *, routine_id, trigger, payload)` — read policy, resolve sinks, deliver, log events.

- [ ] **Step 1: Write failing tests** — webhook to allow-listed URL succeeds; non-allow-listed URL rejected with WEBHOOK_URL_NOT_ALLOWED; HMAC signature present; delivery logged.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** sink + service with allow-list validation.
- [ ] **Step 4: Run GREEN and commit** — `feat: add webhook notification service (phase 6d)`.

### Task 3: Attention inbox service

**Files:** Create `relay/attention/service.py`; Create `tests/test_phase6d_attention.py`

**Interfaces:**
- `AttentionService.list(db, *, kind=None, limit=50) -> list` — aggregates failed Runs, low-quality Runs (if 6c present), awaiting-approval nodes (if 6a present).

- [ ] **Step 1: Write failing tests** — failed Task Run appears; awaiting-approval step appears; filter by kind works.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** as a read model over existing tables.
- [ ] **Step 4: Run GREEN and commit** — `feat: add needs attention inbox (phase 6d)`.

### Task 4: Dashboards

**Files:** Create `relay/operations/service.py`; Create `tests/test_phase6d_dashboards.py`

**Interfaces:**
- `routine_dashboard(db, limit=50) -> list` — per-Routine: last success/failure, success rate, attention count.
- `project_dashboard(db, limit=50) -> list`.

- [ ] **Step 1: Write failing tests.**
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** aggregate queries over project_runs and routine_runs.
- [ ] **Step 4: Run GREEN and commit** — `feat: add routine and project dashboards (phase 6d)`.

### Task 5: API + daemon routes + CLI

**Files:** Modify `relay/api.py`, `relay/daemon.py`, `relay/cli.py`; Create `tests/test_phase6d_api.py`, `tests/test_phase6d_cli.py`

- [ ] **Step 1: Write failing tests.**
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add routes** (`POST /v1/notifications/test`, `GET /v1/attention`, `GET /v1/operations/routines`, `GET /v1/operations/projects`, `GET /v1/notifications/events`) and CLI (`relay attention list`, `relay operations routines`, `relay operations projects`, `relay notify test`).
- [ ] **Step 4: Run GREEN and commit** — `feat: expose observability API and CLI (phase 6d)`.

### Task 6: Final verification

- [ ] Full suite + Ruff + compileall + git diff --check + log.md.

## Stop Gate

- Notification policies fire webhook deliveries on configured triggers.
- Webhook delivery respects allow-list, signs payloads, logs attempts.
- Needs Attention inbox aggregates failed/low-quality/awaiting-approval.
- Dashboards report success rate and attention counts.
- Full suite, Ruff, compileall pass.
