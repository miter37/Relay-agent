# Relay cross-platform operation

Relay 0.5 supports Windows 11, mainstream Linux distributions, and macOS using the same Python codebase.

## Common requirements

- Python 3.11 or later
- The selected provider CLI installed and logged in for that OS user
- `relay doctor --worker <name> --deep` passed for the installed CLI version
- A dedicated low-privilege OS account for unattended Hermes/service workloads

## Default Relay home

| OS | Default |
|---|---|
| Windows | `%LOCALAPPDATA%\Relay` |
| Linux | `~/.relay` |
| macOS | `~/Library/Application Support/Relay` |

Set `RELAY_HOME` to use a different location on any platform.

## Process-tree termination

- Windows: Job Object, with `taskkill /T /F` as a fallback.
- Linux/macOS: each provider starts in a new POSIX session; cancellation and timeout terminate its process group with `SIGTERM`, followed by `SIGKILL` if needed.

## Daemon

The daemon binds only to `127.0.0.1` and uses a token file. `relay submit` starts it automatically on all three operating systems. The daemon is a detached user process; no system service is required.

## Linux/macOS installation

```sh
chmod +x scripts/install_unix.sh
./scripts/install_unix.sh
```

The default executable location is `~/.local/bin`. Set `INSTALL_DIR`, `RELAY_HOME`, or `PYTHON` before running the installer to override them.

```sh
INSTALL_DIR="$HOME/bin" RELAY_HOME="$HOME/relay-data" ./scripts/install_unix.sh
```

## Provider differences

Relay does not assume flags are identical across OSes or CLI versions. Each installed binary must pass `doctor --deep`; the resulting adapter spec is tied to its exact executable path and reported version.

Antigravity remains opt-in on all platforms until deep doctor and the local security review pass.

## OS-level isolation

Relay limits requested input/output paths, but provider CLIs still possess the permissions of the OS account running them.

- Windows: dedicated local user plus NTFS ACLs.
- Linux: dedicated unprivileged user, `chmod 700` Relay home, and no access to personal SSH/cloud credentials.
- macOS: dedicated standard user where practical, Relay home owner-only, and no Full Disk Access.

Container or VM isolation may be added by operators, but Relay does not create one automatically.
