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


## Adding New Workers (elay add-agent)

Beyond the three built-in CLIs (claude, codex, ntigravity), any external AI CLI that obeys the Relay worker contract — read equest.md, write a JSON/TXT result file, and (optionally) emit artifacts under the provided directory — can be registered as a worker:

`sh
relay add-agent <worker-id>
`

The wizard prompts for an ID, executable, command template (using {cli}, {request_file}, {result_file}, {artifact_dir}, {model} placeholders), default model, and optional advanced fields. After all inputs are collected the wizard runs elay doctor --deep as a health check; on failure, **nothing is persisted**.

For scripted/CI registration pass --yes and supply RELAY_ADD_AGENT_* environment variables (ID, DISPLAY_NAME, COMMAND, COMMAND_TEMPLATE, DEFAULT_MODEL, REQUIRE_DEEP, ENABLE, plus optional DESCRIPTION, EXTRA_ARGS, MAX_TURNS, TIMEOUT_SECONDS). See elay add-agent --help for the complete list.

Note: workers registered by dd-agent go through the standard Doctor deep probe. If the underlying CLI is later upgraded, re-run elay doctor --worker <id> --deep to refresh the per-version capability spec; dd-agent does not auto-detect upgrades.
