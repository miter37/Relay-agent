# Relay 0.5.0 release notes

## Added

- Automatic periodic cleanup of expired workspace and staging directories.
- Separate retention periods for completed, partial, failed, and cancelled jobs.
- Opportunistic cleanup for sync-only users before new `run` or `submit` work.
- Persistent cleanup state and a cross-process cleanup lock.
- Orphan workspace cleanup for directories no longer represented in SQLite.
- `relay cleanup --status` to inspect policy and last run.
- Linux and macOS installation/uninstallation scripts.
- macOS default Relay home at `~/Library/Application Support/Relay`.
- Cross-platform and automatic-cleanup documentation.
- Linux/macOS security guidance.

## Preserved

Automatic cleanup removes only internal workspace/staging copies. Delivered results, delivered artifacts, SQLite history, and adapter audit specs are not removed.

## Process handling

- Windows continues to use Job Objects with `taskkill /T /F` fallback.
- Linux and macOS use new POSIX sessions and process-group termination.

## Validation status

- Python unit tests passed on Linux using mock Claude, Codex, and Antigravity CLIs.
- The Unix installer was smoke-tested in an isolated temporary home.
- Actual provider CLI behavior still requires `doctor --deep` on each target machine and OS.
