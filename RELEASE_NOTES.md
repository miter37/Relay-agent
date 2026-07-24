# Relay 1.1.0 release notes

Relay 1.1.0 adds Custom Agent Apps across the CLI, GUI, and Schedules.

## Added

- Manifest-backed custom Agent registration, editing, deep testing, enable/disable, and recoverable deletion.
- A shell-free generic CLI adapter with validated argv placeholders and normalized text or JSON results.
- Agent App model discovery and executable-version invalidation that requires changed Agents to be retested.
- Agent App management in Settings and custom Agent selection for ordinary and scheduled Jobs.
- API schema revision 5 with GUI minimum version 1.1.0.

## Fixed

- GUI daemon requests now recognize successful Qt network replies and enforce their configured timeout.
- Schedule timezone data is installed on Windows.
- Daemon shutdown waits for scheduler workers before temporary Relay Homes are removed.
- Schedule snapshots and retention accept macOS system path aliases while continuing to reject symlinks inside Relay-managed roots.
- Agent App editing preserves advanced manifest fields and invalidates stale GUI test results after any definition change.
- Pre-save Agent tests use expiring definition-bound tokens without leaving cancelled Agent Apps behind.
- Custom Agent subprocesses inherit only operational and explicitly declared environment variables.

## Verification

GitHub Actions checks Ruff formatting and lint, the full test suite on Windows, Ubuntu, and macOS with Python 3.11–3.13,
and real daemon-backed GUI smoke coverage.

---

# Relay 1.0.0 release notes

Relay 1.0.0 adds the G4 Schedule GUI and daemon auto-start controls.

## Added

- Schedule editor with Daily, Weekly, Monthly, Every-N-days, and one-time rules.
- Required next-run preview before a Schedule can be saved.
- Schedule sidebar, detail tabs, run history, pause/resume, Run now, edit, copy, delete, and output-folder actions.
- Schedule eligibility action from replayable completed Jobs.
- Settings auto-start status and authenticated enable/disable controls for Windows, Linux systemd-user, and macOS LaunchAgent adapters.
- One-time auto-start prompt after creating the first Schedule.
- API schema revision 4 with GUI minimum version 1.0.0.

## Compatibility

- Schedule definitions remain separate from ordinary Job history; every scheduled occurrence is still an ordinary Job.
- Schedule deletion is a soft delete and preserves existing Jobs and outputs.
- Existing G3 CLI and daemon Schedule APIs remain available.

## Verification

The full 160-test suite, Ruff checks, and GUI offscreen tests pass locally.

---

# Relay 0.9.0 release notes

Relay 0.9.0 adds the G3 Schedule core for reliable daemon-managed recurring
Jobs.

## Added

- Daily, weekly, monthly, every-N-days, and one-time schedules with IANA timezone and DST-aware occurrence calculation.
- Schedule creation from completed replayable Jobs with immutable task and attachment snapshots.
- Authenticated Schedule lifecycle API and `relay schedule` CLI commands for preview, control, manual runs, and history.
- Atomic occurrence claims, overlap/missed-run policies, unique output directories, and normal Job-history linkage.
- Schedule-specific retention for days, latest runs, and forever policies with safe ownership and symlink checks.

## Compatibility

- Scheduled work is represented as ordinary queued Jobs with `caller=schedule` and `submitted_via=schedule`.
- Existing CLI, daemon, G2 GUI, Job history, and ordinary workspace cleanup remain available.
- Schedule GUI creation and editing remain deferred to G4.

## Verification

The full test suite, Ruff checks, compileall, and release build pass locally.

---

# Relay 0.8.0 release notes

Relay 0.8.0 adds the first GUI write and job-control workflow.

## Added

- New Task screen with task text/file input, attachments, Agent, model, profile, timeout, output, and overwrite options.
- GUI Job creation through the authenticated daemon API.
- Stop task and Run again actions with replay/privacy gating.
- Job detail tabs for overview, task, progress, result, files, logs, and events.
- Incremental log tailing and bounded result previews.
- GUI Agent registry endpoint and GUI/daemon API schema revision 3.

## Compatibility

- GUI-created Jobs remain visible to CLI history and status commands.
- GUI write actions are disabled when daemon compatibility checks fail.
- Non-replayable Jobs cannot be rerun.
- Existing CLI and daemon endpoints remain available.

## Verification

The full test suite and Ruff checks pass locally. GitHub Actions verifies
the same result on Windows, Ubuntu, and macOS across supported Python versions.

---

## Historical G0: Relay 0.6.0

Relay 0.6.0 was the G0 foundation release for the desktop GUI roadmap.

## Added

- Versioned SQLite migration with pre-migration backups.
- Committed Relay 0.5.0 empty and populated database fixtures.
- Job history metadata for titles, previews, source labels, replayability, and future Schedule links.
- `/health` compatibility fields for daemon version, API versions, schema revision, minimum GUI version, and Relay Home identity.
- Read-only `/v1/jobs` and `/v1/jobs/{job_id}` APIs with filtering, search, and cursor pagination.
- Compatibility decision helper and built-in Agent registry interface.

## Compatibility

- Existing CLI and daemon endpoints remain available.
- Existing Relay 0.5.x databases are upgraded without deleting Job history.
- Schedule tables and the desktop GUI remain planned for later releases.

## Verification

The full test suite and Ruff checks pass locally. GitHub Actions must verify
the same result on Windows, Ubuntu, and macOS across Python 3.11–3.13 before
the v0.6.0 tag is published.

---

## Historical v0.5.1 notes

The following notes are retained from the v0.5.1 release for historical
reference. No GitHub release existed before v0.5.1, so
everything documented for 0.5.0 — automatic cleanup, Linux/macOS installers,
`relay add-agent`, the English `--help` overhaul — ships here as well.

It is also the first release built automatically and verified on Windows,
macOS, and Linux.

## Fixed

- A fresh clone could not be installed: the installers expected `relay.pyz` to
  be in the repository. They now build it when it is missing.
- 9 of 60 tests failed on Windows, because the test fixtures always invoked the
  POSIX mock CLIs. The suite now selects the `.cmd` wrappers on Windows.
- The Windows mock wrappers all reported themselves as `claude`, which
  invalidated worker-fallback and `doctor --deep` results on Windows.
- `SHA256SUMS.txt` was maintained by hand and had gone stale. It is now
  generated together with `relay.pyz`, so the two cannot disagree.

## Added

- CI: every push and pull request runs the full suite on Windows, macOS, and
  Linux across Python 3.11–3.13.
- Automated releases: pushing a `v*` tag verifies, tests, builds, and
  publishes. Any check failing stops the run before the release is created.
- `workers.claude.permission_mode` is now configurable. The default is
  unchanged (`bypassPermissions`).
- `relay doctor` warns when a worker bypasses permission checks and no
  OS-level isolation has been recorded. Advisory only.
- `SECURITY.md`, `CODEOWNERS`, Dependabot, issue labels, and `ruff` linting.
  Development tooling only — Relay still has zero runtime dependencies.

## Changed

- `relay.pyz` and `SHA256SUMS.txt` are no longer tracked in the repository.
  They are published only as release assets.

## Verification

66 tests pass on Windows, macOS, and Linux across Python 3.11–3.13.

Provider CLI behavior still requires `relay doctor --worker <worker> --deep` on
each target machine, and again whenever the underlying CLI is upgraded.
