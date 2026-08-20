# Startup Worker Audit Design

## Purpose

A Task Run fails immediately with `WORKER_UNVERIFIED` when the requested worker (and every worker in its fallback chain) lacks a current deep capability audit (`deep_ok`). Today the only way to clear this is a manual `relay doctor --worker <id> --deep` per worker, run after the fact — the first real Task Run after a fresh install, a worker CLI upgrade, or an Agent App edit is the one that discovers the gap and fails. This design adds an automatic deep-audit pass that runs in the background whenever the daemon starts, so most workers are already verified by the time a real job needs them.

This only primes the existing verification gate; it does not change what verification means or when `WORKER_UNVERIFIED` is raised (`AdapterBase.require_verified()`, `relay/adapters/base.py:143-169`).

## Scope Decisions

- **Target workers**: built-in workers (`claude`, `codex`, `antigravity`) plus **enabled** custom Agent Apps only, via `AgentRegistry.list_enabled_agents()` (`relay/agent_registry.py:58-59`). Disabled Agent Apps are never audited automatically — auditing means actually executing that App's real argv, and AGENTS.md ties deep audit to enablement, not registration. A disabled App still requires its existing manual audit-then-enable flow.
- **Skip already-verified workers**: before attempting anything, a worker whose current spec already satisfies `require_verified()` (matching version, matching executable path, `deep_ok` true, and for Agent Apps a matching `_definition_hash`) is skipped entirely.
- **Retry policy**: up to 3 consecutive deep-audit attempts per worker, all within the same daemon startup pass. If all 3 fail, that worker is left unverified until the next daemon restart (or a manual `relay doctor --deep`) — no cross-restart attempt counter, no persisted "give up" state beyond what the spec file already records.
- **Shallow-failing workers are excluded from retry**: `Doctor.audit_adapter(adapter, deep=True)` (`relay/doctor.py:39-51`) only attempts the real deep probe if `shallow_audit()` passed. If shallow fails (CLI not installed, `--version`/`--help` broken), that means retrying deep audit is meaningless — move on to the next worker with zero retries, don't burn the 3-attempt budget.
- **Sequential across workers**: workers are processed one at a time, not in parallel, to avoid concurrent CLI subprocesses competing for the same API rate limits/credentials.
- **Non-blocking**: the daemon accepts and dispatches jobs immediately; this audit pass runs on its own background thread. A job that lands on a worker mid-audit (or not yet audited) still gets the existing `WORKER_UNVERIFIED` behavior — this feature reduces how often that happens, it does not change job-dispatch semantics.

## Architecture

Add a new background loop, `StartupAuditLoop`, in `relay/daemon.py`, following the existing `MaintenanceLoop` pattern (`relay/daemon.py:199-232`): a single `threading.Thread`, entire run body wrapped in `try/except Exception` so an audit failure or crash never takes down the daemon. Unlike `MaintenanceLoop` (which loops forever on an interval), `StartupAuditLoop` runs its worker sweep once per daemon process lifetime and then exits its thread.

Lifecycle integration: constructed and `.start()`ed in `RelayDaemon.serve()` (`relay/daemon.py:1262-1280`) alongside `Scheduler`, `ScheduleLoop`, and `MaintenanceLoop` (`relay/daemon.py:1270-1274`), and `.stop()`ed in the same `finally` block (`relay/daemon.py:1277-1281`) so a shutdown mid-sweep terminates cleanly (the loop checks a `stop_event` between workers and between attempts).

Per-worker sequence inside the thread:

1. Resolve the worker's adapter and current spec.
2. If `require_verified()` succeeds already, skip to the next worker.
3. Otherwise, call `Doctor.audit_adapter(adapter, deep=True)` (`relay/doctor.py:39-51`) — this persists the result to the spec file (`{RELAY_HOME}/adapter-specs/{worker}/{version}.json`) and to the `capability_audits` table (`relay/db.py:186-196`) on every attempt, exactly as a manual `relay doctor --deep` run would. No new persistence code is needed; this feature only adds a caller.
4. If the shallow portion of that result failed, stop retrying this worker and move on.
5. If deep succeeded, move on.
6. If shallow passed but deep failed, retry (step 3) up to a total of 3 attempts for this worker before moving on.

## Configuration

Add `startup_audit_enabled` (boolean, default `true`) to config, following the existing convention of boolean daemon-behavior toggles like `daemon_auto_start` (`relay/config.py:35`). When `false`, `RelayDaemon.serve()` does not construct/start `StartupAuditLoop` at all. This exists so test harnesses and CI, which start many short-lived daemon instances, can opt out without relying on shallow-audit failures to keep the loop cheap.

## Logging

Each attempt (skip, shallow-failure, deep success, deep failure, retry) is logged at the daemon's existing logger so an operator can see in the daemon log why a worker did or didn't get auto-verified, without needing a new GUI surface. No new GUI status indicator is added in this design — the existing `relay doctor` output and the GUI's existing health panel/timer (`relay/gui/main_window.py`, `health_timer`) already reflect the spec file state once this loop updates it.

## Testing Considerations

- In sandboxed/CI environments without the real worker CLIs installed, `shallow_audit()` fails fast (executable resolution only) for every target worker, so the loop's total added runtime is small and no real subprocess is spawned.
- Existing tests that construct `RelayDaemon` instances directly (bypassing `serve()`) are unaffected, since `StartupAuditLoop` is only started from `serve()`.
- Tests that do exercise `serve()` and start real daemon processes should set `startup_audit_enabled=false` in their config fixtures if audit noise or timing becomes a problem; this is a follow-up to verify during implementation, not a blocking unknown for this design.
- Add a regression test asserting: (a) a worker with a valid current spec is not re-audited, (b) a worker whose shallow audit fails gets exactly one audit attempt, not three, (c) a worker whose shallow passes but deep fails is attempted exactly 3 times, (d) disabled Agent Apps are never included in the target list.

## Non-Goals

- No change to `WORKER_UNVERIFIED` semantics or to manual `relay doctor` behavior.
- No periodic re-audit while the daemon keeps running — this is a startup-only sweep, not a `MaintenanceLoop`-style recurring job.
- No new GUI screen or endpoint for audit progress.
- No cross-restart attempt-count persistence.
