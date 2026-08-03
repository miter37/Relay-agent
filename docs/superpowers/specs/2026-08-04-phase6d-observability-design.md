# Phase 6d Operational Observability Design

**Date:** 2026-08-04
**Status:** Approved
**Source:** Phase 6 (6d) of docs/relay_product_direction_v1.0.md

## 1. Goal

Give operators a notifications layer (webhook delivery), a Needs Attention inbox that aggregates items requiring human action (failed Runs, low quality, pending approvals), and operational dashboards for Routine and Project health.

## 2. Scope

6d includes:

- Notification policy enforcement for Routine and Project (the policy stored in Phase 5 is now acted upon).
- Webhook delivery sink (HTTP POST with retry and a delivery log) behind an allow-list.
- Needs Attention inbox: an aggregated query over failed/low-quality/awaiting-approval items.
- Routine and Project operational dashboards: success rate, last run, drift, attention counts.
- Authenticated daemon APIs and machine-readable CLI commands.

6d excludes:

- Email/Slack native sinks (webhook is the generic primitive; specific sinks layer on top later).
- Approval timeout automation (approval waiting is surfaced; auto-escalation is later).
- GUI dashboards (deferred).

## 3. Notification Policy Enforcement

Phase 5 stored `notification_policy_json` on Routines and Projects. 6d enforces it:

- `on_failure`, `on_low_quality`, `on_attention` triggers.
- Each trigger maps to one or more webhook sinks.
- Delivery is best-effort with a retry policy; delivery results are recorded as notification events.

## 4. Webhook Sink

A webhook sink definition: `{"kind": "webhook", "url": "...", "secret": "..."}`. URLs must match an allow-list of hosts/schemes (default 127.0.0.1 only unless the operator expands it). Delivery signs the payload with HMAC-SHA256 using the secret. A delivery log records attempts, status codes, and final outcome.

## 5. Needs Attention Inbox

A read model aggregating:

- failed Task/Project Runs.
- low-quality Runs (from 6c).
- awaiting_approval checkpoint nodes (from 6a).
- Routines whose last run failed.

`GET /v1/attention` returns a paginated, filterable list. Each item links to the owning Run/Routine/Project.

## 6. Dashboards

`GET /v1/operations/routines` and `/v1/operations/projects` return aggregate health:

- total/running/failed counts.
- last success and last failure timestamps.
- success rate over a configurable window.
- current attention count.

Dashboards are computed on read from existing tables; no denormalized counters to keep in sync.

## 7. API

```text
POST   /v1/notifications/test   {"sink": {...}}
GET    /v1/attention?kind=failed&limit=50
GET    /v1/operations/routines
GET    /v1/operations/projects
GET    /v1/notifications/events?routine_id=...
```

Stable errors: WEBHOOK_URL_NOT_ALLOWED, WEBHOOK_DELIVERY_FAILED.

## 8. CLI

```text
relay attention list [--kind failed|low_quality|approval]
relay operations routines
relay operations projects
relay notify test --url https://... [--secret ...]
```

## 9. Completion Criteria

- Notification policies on Routines/Projects fire webhook deliveries on the configured triggers.
- Webhook delivery respects the allow-list, signs payloads, and logs attempts.
- Needs Attention inbox aggregates failed/low-quality/awaiting-approval items.
- Routine and Project dashboards report success rate and attention counts.
- CLI, daemon API, service, and delivery log each have focused tests; full suite, Ruff, and compileall pass.
