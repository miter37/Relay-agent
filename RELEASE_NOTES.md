# Relay 0.5.1 release notes

**This is the first published release.** No GitHub release existed before, so
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
