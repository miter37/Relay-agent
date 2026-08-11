# Phase 6e Data Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Add deterministic export/import (definitions + selected Runs/Artifacts), backup verification, and receipt schema versioning.

**Architecture:** An ExportService serializes Tasks/Projects/Routines and opt-in Runs/Artifacts into a deterministic directory tree. An ImportService restores into a target Home with conflict handling. Receipts gain receipt_schema_version for forward compatibility.

**Tech Stack:** Python 3.11+, tarfile/zipfile, json, hashlib, SQLite, unittest, Ruff.

## Global Constraints

- Phase 6e is daemon/API/CLI core only; no GUI.
- Export is deterministic (sorted keys, sorted files) so two exports of the same data are byte-identical.
- Import never overwrites an existing user DB in place; it writes into the target after migration.
- Secrets/credentials are never exported or imported.
- Receipts carry receipt_schema_version; consumers tolerate unknown forward fields.
- `relay-receipt.json`, `test_result.json`, `test_task.md` never staged.
- Version stays 1.1.0; work on `feat/phase0-domain-compat`.

---

### Task 1: Receipt schema versioning

**Files:** Modify `relay/db.py` (add `receipt_schema_version` column to jobs and project_runs), `relay/api.py`; Create `tests/test_phase6e_receipt.py`

- [ ] **Step 1: Write failing tests** — new Runs carry receipt_schema_version; old receipt consumers tolerate unknown forward keys.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add column (additive), stamp version on Run creation, expose in receipt APIs.**
- [ ] **Step 4: Run GREEN and commit** — `feat: add receipt schema versioning (phase 6e)`.

### Task 2: Export service

**Files:** Create `relay/lifecycle/export_service.py`; Create `tests/test_phase6e_export.py`

**Interfaces:**
- `ExportService.export(db, config, *, include_runs=False, out_path) -> Path` — deterministic archive with manifest.json, tasks/, projects/, routines/, optional runs/ and artifacts/.

- [ ] **Step 1: Write failing tests** — export produces deterministic archive; manifest lists content hashes; two exports of same data are byte-identical.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** using sorted canonical_json + sorted file listing + zipfile.
- [ ] **Step 4: Run GREEN and commit** — `feat: add deterministic export service (phase 6e)`.

### Task 3: Import service

**Files:** Create `relay/lifecycle/import_service.py`; Create `tests/test_phase6e_import.py`

**Interfaces:**
- `ImportService.import_archive(db, config, archive_path, *, conflict="skip", include_runs=False) -> dict` — restore definitions with conflict policy; opt-in Run/Artifact restore with collision handling; verify content hashes; never import secrets.

- [ ] **Step 1: Write failing tests** — import definitions skip/overwrite/rename; round-trip integrity check; secret fields are absent.
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Implement** import with manifest verification.
- [ ] **Step 4: Run GREEN and commit** — `feat: add import service with conflict handling (phase 6e)`.

### Task 4: API + daemon routes + CLI

**Files:** Modify `relay/api.py`, `relay/daemon.py`, `relay/cli.py`; Create `tests/test_phase6e_api.py`, `tests/test_phase6e_cli.py`

- [ ] **Step 1: Write failing tests.**
- [ ] **Step 2: Run RED.**
- [ ] **Step 3: Add routes** (`POST /v1/export`, `POST /v1/import`, `GET /v1/receipt-schema`) and CLI (`relay export`, `relay import`, `relay receipt-schema`).
- [ ] **Step 4: Run GREEN and commit** — `feat: expose export/import API and CLI (phase 6e)`.

### Task 5: Final verification

- [ ] Full suite + Ruff + compileall + git diff --check + log.md.

## Stop Gate

- Export produces deterministic archive of definitions and selected Runs/Artifacts.
- Import restores with collision handling and no secret leakage.
- Round-trip export+import verifies integrity.
- Receipts carry and honor receipt_schema_version.
- Full suite, Ruff, compileall pass.
