# Antigravity CLI and Model Verification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the documented Relay model-discovery commands, Antigravity-backed CLI options, and documented failure paths, then fix only reproducible Relay defects.

**Architecture:** Use a fresh temporary `RELAY_HOME` for isolated CLI tests and the installed `agy` executable for live worker calls. Exercise model discovery separately from task execution, capture machine-readable receipts and exit codes, and compare every result against the documented contract. Keep provider quota/auth failures distinct from Relay defects.

**Tech Stack:** Python 3, Relay CLI, SQLite, installed Antigravity CLI (`agy`), unittest.

## Global Constraints

- Do not commit or push changes.
- Use Antigravity for live task execution as requested.
- Do not enable unsafe sandbox bypasses beyond the existing Antigravity configuration.
- Preserve existing user changes and generated release artifacts.
- Record Claude quota/auth limitations instead of treating them as Relay failures.

---

### Task 1: Inventory documented surfaces

**Files:**
- Read: `manual.md`, `README.md`, `docs/CAPABILITY_AUDIT.md`, `docs/DEVELOPMENT_PLAN.md`
- Create: `docs/superpowers/plans/2026-07-23-agy-cli-model-verification.md`

- [x] Enumerate documented commands and flags from the manual and parser help.
- [x] Map model commands to `models --worker`, `--refresh`, `--include-hidden`, `--verify`, and `model-check`.
- [x] Map task options to task-file, worker, fallback, format, output/artifact paths, profile, timeout, caller, request-id, attachments, workspace, overwrite, force-new, model, and machine mode.
- [x] Map documented error paths to invalid paths, service isolation, worker gating, duplicate request IDs, cancellation, timeout, invalid output, and missing job IDs.

### Task 2: Run model discovery and model-check matrix

**Files:**
- Read: `relay/cli.py`, `relay/model_discovery.py`, `relay/adapters/antigravity.py`, `relay/adapters/codex.py`, `relay/adapters/claude.py`
- Test output: isolated temporary Relay home and captured JSON logs

- [ ] Run `models` for all workers and each worker with cache, refresh, hidden, verify, and machine combinations.
- [ ] Run `model-check` for each installed worker using a discovered model and an invalid model.
- [ ] Record whether failures are provider quota/auth/network failures or Relay contract failures.

### Task 3: Run Antigravity task-option matrix

**Files:**
- Read: `relay/cli.py`, `relay/engine.py`, `relay/daemon.py`, `relay/delivery.py`
- Test output: isolated temporary output/artifact directories and captured receipts

- [ ] Exercise synchronous JSON and TXT runs, task-file input, explicit worker, no-fallback, profile, timeout, caller, request-id, attachment, workspace, overwrite, force-new, model, and machine output.
- [ ] Exercise asynchronous submit, status, wait, result, logs/show/history, and daemon lifecycle paths needed by the manual.
- [ ] Verify result files, artifact manifests, request-id behavior, and output paths—not only exit codes.

### Task 4: Run documented error and safety matrix

**Files:**
- Read: `relay/errors.py`, `relay/security.py`, `relay/engine.py`, `relay/validation.py`
- Test output: captured nonzero exits and machine-readable error objects

- [ ] Test unknown job IDs, malformed/missing task files, invalid output/artifact paths, path escape, missing attachments, duplicate request ID conflict, queued cancellation, timeout, and invalid provider output.
- [ ] Test Hermes/service isolation acknowledgement and Antigravity enablement gating.
- [ ] For each failure, confirm stable error code, no unsafe delivery, and no orphaned job state.

### Task 5: Fix and regression-test defects

**Files:**
- Modify: the smallest affected Relay module
- Test: `tests/test_relay.py`

- [ ] For each reproducible Relay defect, add one failing regression test before implementation.
- [ ] Implement one minimal fix at a time and rerun the focused test.
- [ ] Rerun the full test suite after all fixes; do not change provider policy merely to hide provider failures.

### Task 6: Final evidence and documentation

**Files:**
- Modify: `docs/TEST_REPORT.md` if the verified matrix changes its claims
- Rebuild: `relay.pyz` only if source changes require it

- [ ] Run the full unit suite, package smoke test, checksum check, and `git diff --check`.
- [ ] Remove only generated temporary test workspaces and caches created by this verification.
- [ ] Report passed, failed, skipped, and provider-limited cases separately.
