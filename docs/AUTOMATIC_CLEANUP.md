# Automatic workspace cleanup

Relay automatically deletes expired **workspace and staging directories only**. It does not delete:

- delivered result files
- delivered artifact directories
- SQLite job history
- adapter audit specifications

## Default retention

| Job status | Workspace retention |
|---|---:|
| Completed | 7 days |
| Partial | 14 days |
| Failed | 30 days |
| Cancelled | 14 days |
| Orphan directory with no DB job | 7 days |

Active jobs are never removed.

## How it runs

1. When the daemon is active, a maintenance thread checks once per hour whether the configured cleanup interval has elapsed.
2. For users who only use synchronous `relay run`, a due cleanup is performed opportunistically before a new `run` or `submit`.
3. A cross-process lock prevents two cleanup processes from deleting the same workspace concurrently.
4. The last cleanup report is stored in `runtime/cleanup-state.json`.

## Configuration

```toml
cleanup_enabled = true
cleanup_interval_hours = 24
cleanup_run_on_daemon_start = true
cleanup_remove_empty_parents = true
cleanup_remove_orphans = true
cleanup_orphan_days = 7
retention_days_completed = 7
retention_days_partial = 14
retention_days_failed = 30
retention_days_cancelled = 14
```

## Commands

Show the policy and last automatic run:

```sh
relay cleanup --status
```

Preview current deletions:

```sh
relay cleanup --dry-run
```

Run the normal status-specific policy immediately:

```sh
relay cleanup
```

Override all status retention periods for one manual run:

```sh
relay cleanup --days 3
```
