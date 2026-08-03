# Phase 6e Data Lifecycle Design

**Date:** 2026-08-04
**Status:** Approved
**Source:** Phase 6 (6e) of docs/relay_product_direction_v1.0.md

## 1. Goal

Provide export/import and backup for Relay Home data (definitions, Runs, Artifacts, lineage, Routines) and introduce receipt schema versioning so receipts stay forward-compatible across releases.

## 2. Scope

6e includes:

- `relay export`: serialize Tasks, Projects, Routines, selected Runs and their Artifacts into a deterministic archive.
- `relay import`: restore definitions and (optionally) Runs/Artifacts into a fresh or existing Relay Home with collision handling.
- Backup verification (round-trip integrity check).
- Receipt schema versioning: every receipt carries a `receipt_schema_version`; consumers tolerate unknown forward fields.
- Authenticated daemon APIs and machine-readable CLI commands.

6e excludes:

- Incremental/streaming replication to a remote Relay instance.
- Cross-version schema downgrade (imports only upgrade into the current schema).
- Export of credentials/secrets (never included).

## 3. Archive Format

A deterministic tar-like structure (a directory tree zipped) containing:

- `manifest.json` with archive schema version, source relay version, and a content hash list.
- `tasks/`, `projects/`, `routines/` definition JSON files.
- `runs/<run_id>.json` with the Run row and its receipt.
- `artifacts/<artifact_uid>` file bytes plus a sidecar `.meta.json`.
- `lineage.json` for artifact connections.

Export is deterministic (sorted keys, sorted file lists) so two exports of the same data are byte-identical and diffable.

## 4. Import Semantics

- Definitions import by `name` with conflict policy: skip, overwrite, or rename.
- Run/Artifact import is opt-in (`--include-runs`); Artifacts are restored under a fresh artifact root with new UIDs if a collision is detected, preserving lineage links.
- Import never overwrites an existing user DB in place; it writes into the target Relay Home after the standard migration runs.
- Secrets are never imported; a missing secret is reported, not silently empty.

## 5. Receipt Schema Versioning

Every receipt gains `receipt_schema_version` (integer). The current version is bumped when receipt shape changes. Consumers ignore unknown forward keys and surface a warning only if a required field is missing at a known lower version. The version is recorded on the Run row and surfaced in all receipt-bearing APIs.

## 6. API

```text
POST /v1/export   {"include_runs": bool, "filter": {...}}  -> archive path
POST /v1/import   {"archive_path": "...", "conflict": "skip|overwrite|rename", "include_runs": bool}
GET  /v1/receipt-schema
```

Stable errors: EXPORT_FAILED, IMPORT_ARCHIVE_INVALID, IMPORT_CONFLICT, RECEIPT_SCHEMA_UNSUPPORTED.

## 7. CLI

```text
relay export [--include-runs] [--out archive.zip]
relay import <archive> [--conflict skip|overwrite|rename] [--include-runs]
relay receipt-schema
```

## 8. Completion Criteria

- Export produces a deterministic archive of definitions and selected Runs/Artifacts.
- Import restores definitions and (optionally) Runs/Artifacts with collision handling and no secret leakage.
- A round-trip export then import verifies content integrity.
- Receipts carry and honor receipt_schema_version.
- CLI, daemon API, service, and integrity checks each have focused tests; full suite, Ruff, and compileall pass.
