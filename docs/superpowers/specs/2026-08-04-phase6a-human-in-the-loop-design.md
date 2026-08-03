# Phase 6a Human-in-the-loop Design

**Date:** 2026-08-04
**Status:** Approved
**Source:** Phase 6 (6a) of docs/relay_product_direction_v1.0.md; Phase 4 design section 9.4

## 1. Goal

Let a Project Run pause at a node for human review (approve/modify/reject), record the human decision as an Artifact in lineage, and deliver the approved result to an external folder or system. This closes the Human Checkpoint deferral made in Phase 4.

## 2. Scope

6a includes:

- Approval nodes in Project definitions (a node marked checkpoint).
- Project Run pause into awaiting_approval with approval input manifest.
- Approve, approve-with-edits, and reject operations on a paused Project Run.
- Human edits recorded as a new Artifact with producer=human and full lineage.
- Post-approval delivery to an allow-listed external folder (copy-out) with a delivery record.
- Daemon restart safety (awaiting_approval survives restart).
- Authenticated daemon APIs and machine-readable CLI commands.

6a excludes:

- Approval GUI (deferred to a later phase).
- Delivery to remote systems beyond a local/mounted allow-listed folder (S3/HTTP hooks are 6d notification work).
- Timeouts on approvals (Phase 6d operational layer).

## 3. Project Definition Extension

A Project node gains an optional `checkpoint` object:

```json
{
  "node_id": "review",
  "task_id": "TASK-DRAFT",
  "checkpoint": {
    "enabled": true,
    "deliver_to": [{"kind": "folder", "path": "/relay/deliveries/weekly"}]
  }
}
```

`deliver_to` is validated against the configured external delivery allow-list roots at Project create/update time. Unknown kinds are rejected.

## 4. Runtime Pause

When a checkpoint node reaches `ready` and is dispatched, the Project runtime:

1. Creates the Task Run normally (the draft is produced).
2. After the Task Run completes, transitions the Project Run step to `awaiting_approval` instead of `completed`.
3. Records an approval manifest: which Artifact(s) the reviewer should inspect, the node, and a stable approval_token.

The Project Run stays `running`; the checkpoint step is the only non-terminal step. Descendants stay `blocked` until approval.

## 5. Approve / Edit / Reject

- **approve**: step -> completed; unblock descendants; Project Run continues.
- **approve_with_edits**: reviewer supplies a replacement file; a new Artifact is recorded with `producer=human`, `role` matching the checkpoint's output role, and lineage pointing to the original draft Artifact; step -> completed; descendants use the edited Artifact.
- **reject**: step -> failed with reviewer reason; Project Run -> failed under stop policy.

All three record an approval event with actor, decision, token, and timestamp.

## 6. Post-approval Delivery

On approve/approve_with_edits, each `deliver_to` target runs:

- folder: copy the selected Artifact (edited if present) into the target path under a unique name; verify the path is within an allow-listed root; record a delivery Artifact (role=delivery, producer=human) and a delivery event.

Delivery failures fail the Project Run with a structured error; the approval itself is not rolled back (lineage is preserved).

## 7. Restart Recovery

awaiting_approval is a terminal-ish state for the step but non-terminal for the Project Run. On daemon restart the Project runtime reconciles: it does not re-dispatch the checkpoint node and waits for the explicit approval API call.

## 8. API

```text
GET    /v1/project-runs/{id}/approvals
POST   /v1/project-runs/{id}/approvals/{token}/approve
POST   /v1/project-runs/{id}/approvals/{token}/reject
POST   /v1/project-runs/{id}/approvals/{token}/edit
```

Stable errors: PROJECT_NOT_CHECKPOINT, APPROVAL_NOT_FOUND, APPROVAL_ALREADY_DECIDED, DELIVERY_PATH_NOT_ALLOWED, DELIVERY_FAILED.

## 9. CLI

```text
relay approval list <project-run-id>
relay approval show <project-run-id> <token>
relay approval approve <project-run-id> <token>
relay approval reject <project-run-id> <token> --reason "..."
relay approval edit <project-run-id> <token> --file path --role final_report
```

## 10. Completion Criteria

- A checkpoint node pauses the Project Run in awaiting_approval.
- Approve, edit, reject all produce correct lineage and state transitions.
- Human edits appear as producer=human Artifacts consumed by descendants.
- Delivery to an allow-listed folder succeeds and is recorded.
- Delivery to a non-allow-listed path is rejected.
- Daemon restart preserves awaiting_approval.
- CLI, daemon API, runtime, service, and DB primitives each have focused tests; full suite, Ruff, and compileall pass.
