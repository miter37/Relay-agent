<div align="center">
  <h1>🚀 Relay-agent</h1>
  <p><strong>Run, monitor, and safely deliver work from Claude Code, Codex CLI, Antigravity, and your own agent CLIs.</strong></p>
  <p>Desktop GUI · CLI automation · Local daemon · Persistent job history</p>

  <p>
    <a href="https://github.com/miter37/Relay-agent/actions/workflows/ci.yml"><img src="https://github.com/miter37/Relay-agent/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://github.com/miter37/Relay-agent/releases"><img src="https://img.shields.io/github/v/release/miter37/Relay-agent?style=flat-square" alt="Release"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square" alt="Python"></a>
    <a href="https://github.com/miter37/Relay-agent/blob/master/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License"></a>
    <img src="https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square" alt="OS">
  </p>
</div>

Relay-agent is a local job broker for AI command-line tools. A person can create and inspect jobs in the desktop app, while an automation agent can submit the same work through the CLI. Both paths share one daemon, one SQLite history, and the same validated result-delivery contract.

<p align="center">
  <img src="docs/assets/relay-agent-gui-main.png" alt="Relay-agent desktop dashboard" width="1200">
</p>

<p align="center"><em>Monitor Relay health, search and filter job history, and inspect selected work from the desktop dashboard.</em></p>

> **Reliability boundary:** Relay-agent validates process completion, result-file creation, encoding, schema, artifact paths, and delivery. It does not verify the factual accuracy or reasoning quality of AI-generated content.

## Contents

- [Why Relay-agent](#why-relay-agent)
- [Requirements](#requirements)
- [Quick start: desktop GUI](#quick-start-desktop-gui)
- [Portable and CLI-only installation](#portable-and-cli-only-installation)
- [Desktop workflow](#desktop-workflow)
- [CLI workflow](#cli-workflow)
- [Safe work in a real folder](#safe-work-in-a-real-folder)
- [Custom Agent Apps](#custom-agent-apps)
- [Automation with OpenClaw or Hermes](#automation-with-openclaw-or-hermes)
- [Results, security, and operations](#results-security-and-operations)
- [Documentation](#documentation)

## Why Relay-agent

Relay-agent adds a durable control and delivery layer around powerful AI CLIs.

- **Desktop task control:** Create jobs with task text or a Markdown file, attachments, Agent and model selection, profiles, fallback behavior, time limits, result paths, and artifact folders.
- **One shared job history:** GUI, CLI, and external-agent jobs appear in the same searchable history with status, source, timestamps, attempts, and output locations.
- **Detailed inspection:** Review Overview, Task, Progress, Answer, Result, Files, Logs, and Events without digging through Relay's internal database or workspaces.
- **Non-interrupting progress checks:** Inspect process state, recent activity, stalls, and common error signals without sending another message to the running Agent.
- **Useful job controls:** Stop active work, run completed work again, copy task text, and open result or artifact folders.
- **Built-in and custom Agents:** Use Claude Code, Codex CLI, and Antigravity, or register manifest-backed Agent Apps for other local CLIs.
- **Safe working-folder delivery:** Let an Agent work on an isolated copy, validate the changed-file set, then apply only those changes to a requested real folder.
- **Persistent receipts:** Store job metadata, attempts, failures, and output paths in local SQLite history.
- **Compatibility safety:** GUI write actions are disabled if the desktop app and daemon do not agree on the supported API or Relay Home.
- **Automation-ready:** Submit background jobs, deduplicate external requests, wait for completion, and consume machine-readable receipts.

## Requirements

- Windows 11, Linux, or macOS
- Python 3.11+
- Git for a clone-based source installation
- At least one installed and logged-in Agent CLI:
  - `claude`
  - `codex`
  - `agy` — optional and gated behind additional security verification
- PySide6 6.8+ for the desktop GUI

For unattended external-agent or service operation, use a dedicated low-privilege OS account. Interactive desktop use does not require a separate account.

The CI matrix runs on Windows, macOS, and Linux with Python 3.11–3.13. CI validates Relay with mock provider CLIs; live provider behavior depends on the installed CLI version, account, and platform. Run a deep worker audit on every target machine and after each provider CLI upgrade.

## Quick start: desktop GUI

Install the project and its optional GUI dependency from a clone:

```sh
git clone https://github.com/miter37/Relay-agent.git
cd Relay-agent
python -m pip install ".[gui]"
relay init
```

Verify each Agent you intend to use:

```sh
relay doctor --worker claude --deep
relay doctor --worker codex --deep
```

Then open the desktop app:

```sh
relay --gui
```

The GUI connects to the local Relay daemon and starts it automatically by default. Set `daemon_auto_start` to `false` only when daemon startup is managed separately.

### If the GUI opens in compatibility mode

Compatibility mode protects the job database and disables write actions when the running daemon is older, newer, or using a different Relay Home. Restart the daemon with the same Relay installation:

```sh
relay daemon stop
relay daemon start
relay --gui
```

If the warning remains, confirm that the shell and GUI resolve the same `relay` command and `RELAY_HOME`.

## Portable and CLI-only installation

Relay's core CLI does not require the Qt GUI packages. A fresh clone can build the self-contained `relay.pyz` application with:

```sh
python build_release.py
```

### Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

### Linux and macOS

```sh
chmod +x scripts/install_unix.sh
./scripts/install_unix.sh
```

The installer builds `relay.pyz` when it is not already present, copies it into the user install directory, initializes Relay Home, and adds a launcher. Open a new terminal after installation if the command is not yet on `PATH`.

To add the GUI to a portable installation, install PySide6 into the Python interpreter used by the launcher:

```sh
python -m pip install "PySide6>=6.8,<7"
relay --gui
```

Prebuilt `relay.pyz` and checksum files are also available from the [latest release](https://github.com/miter37/Relay-agent/releases/latest).

## Desktop workflow

### 1. Create a task

Select **+ New Task** and provide as much or as little configuration as needed:

- optional task name
- inline task text or a UTF-8 task file
- attachments
- Agent and model
- execution profile
- fallback preference
- time limit
- JSON or text result
- result and generated-files locations
- optional real working folder
- external request ID and duplicate-control options

<p align="center">
  <img src="docs/assets/relay-agent-new-task.png" alt="Relay-agent New Task form" width="1200">
</p>

<p align="center"><em>The New Task form exposes the same Agent, model, fallback, file, result, and working-folder controls available through the CLI.</em></p>

### 2. Observe the job

The job detail view separates the most useful information:

- **Overview:** status, Agent, model, timestamps, source, output locations, and actions
- **Task:** the submitted instruction
- **Progress:** process and activity diagnostics
- **Answer:** readable final answer with copy support
- **Result:** the complete delivered result
- **Files:** generated artifact metadata
- **Logs:** stdout, stderr, progress-check results, and attempt selection
- **Events:** lifecycle history

Use **Check progress** when a running job appears quiet. Relay inspects the process and its recent activity without messaging or interrupting the Agent.

### 3. Recover or repeat work

Active jobs can be stopped. Completed replayable jobs can be run again, and output folders can be opened directly from the desktop app. Search and filters make older work easier to find by task text, name, Agent, status, source, or time.

## CLI workflow

The desktop app is optional. Every ordinary job can be submitted and inspected from a terminal.

### Synchronous task

```sh
relay run "Review this repository and identify reliability risks" --worker codex
```

`--worker codex` means “try Codex first, then use configured fallbacks if necessary.” Add `--no-fallback` when only that Agent may run.

### Structured task file

```powershell
relay run `
  --task-file "D:\RelayInput\request.md" `
  --attach "D:\RelayInput\report.pdf" `
  --worker claude `
  --format json `
  --machine
```

### Background task

```sh
relay submit \
  --task-file "task.md" \
  --worker codex \
  --request-id "conversation-42-task-7-codex" \
  --no-fallback \
  --machine
```

Then use the returned `job_id`:

```sh
relay wait <job_id> --machine
relay result <job_id> --machine
```

`request_id` is an idempotency key for one logical external request. Reusing it returns the existing job even if a local task file has changed. Use a unique value such as `<conversation>-<task>-<agent>`.

## Safe work in a real folder

Use `--target` when the Agent must create or modify files in an existing directory:

```powershell
relay run "Review and improve this code" `
  --worker codex `
  --target "D:\project" `
  --artifacts "D:\relay-copies"
```

Relay:

1. creates an isolated working copy;
2. runs the Agent inside that workspace;
3. validates paths and the changed-file set;
4. applies only verified changes to the requested folder; and
5. keeps a separate copy of changed or created files in the artifacts location.

`--out` controls the JSON or text result file. `--artifacts` controls generated-file delivery. The GUI exposes the same behavior as **Working folder** and **Files folder**.

## Custom Agent Apps

The built-in Agents cover common workflows, but Relay can manage other local AI CLIs through manifest-backed Agent Apps.

Open **Settings → Agent Apps** to:

- register an executable and argument template;
- choose request and result modes;
- declare supported result formats and artifact support;
- configure model discovery and model arguments;
- declare required environment variable names and safety capabilities;
- run a deep test before saving or enabling the Agent;
- edit, retest, enable, disable, or recoverably delete an Agent App.

Changing a runtime definition invalidates its previous test. The Agent remains unavailable until the changed definition passes another deep test.

CLI inspection and lifecycle commands use the same registry:

```sh
relay agent-app list
relay agent-app show opencode
relay agent-app test opencode
relay agent-app enable opencode
```

The original interactive registration flow remains available:

```sh
relay add-agent opencode
relay doctor --worker opencode --deep
relay run --worker opencode "Hello"
```

Run `relay add-agent --help` and `relay agent-app --help` for the complete command set.

## Automation with OpenClaw or Hermes

Register `skills/hermes-relay/SKILL.md` (skill name: `use_relay_agent`) in the calling environment. An always-on agent can then submit long-running work to one or more worker CLIs and collect the final receipts asynchronously.

For independent multi-Agent comparison, submit one job per Agent with a unique request ID and `--no-fallback`:

```sh
relay submit --task-file task.md --worker claude --request-id arch-claude --caller hermes --no-fallback --machine
relay submit --task-file task.md --worker codex --request-id arch-codex --caller hermes --no-fallback --machine
relay submit --task-file task.md --worker antigravity --request-id arch-antigravity --caller hermes --no-fallback --machine
```

For unattended execution, configure Relay under a dedicated low-privilege account with explicit filesystem allowlists and ACL isolation.

## Results, security, and operations

### JSON result contract

When `--format json` is selected, the delivered result follows this shape:

```json
{
  "schema_version": "1.0",
  "status": "complete",
  "answer": "The requested analysis...",
  "sources": ["https://example.com"],
  "uncertainties": [],
  "missing_items": [],
  "artifacts": [
    {
      "name": "report.md",
      "relative_path": "report.md",
      "description": "Generated report"
    }
  ]
}
```

The Relay receipt status reports whether the CLI execution and delivery completed. The `status` inside the result file is the Agent's self-reported task outcome. Check both.

### Model discovery

```sh
relay models
relay models --worker codex --refresh
relay model-check --worker claude --model sonnet --machine
```

Codex and Antigravity can expose account-aware model catalogs when their CLIs support it. Claude Code does not expose a complete non-interactive account catalog, so Relay may combine configured and known candidates. A failed Claude model probe can also mean expired authentication, quota exhaustion, rate limiting, or a network timeout.

### Security posture

Inspect the current isolation and allowlist state:

```sh
relay security --machine
```

Full Access Mode is a separate per-Agent switch. It disables that Agent CLI's permission checks or sandbox restrictions and applies immediately to the running daemon:

```sh
relay security --worker codex --machine
relay security --enable-full-access codex --machine
relay security --disable-full-access codex --machine
```

The same switches are available in **Settings → General**. Use Full Access Mode only for trusted tasks under a dedicated low-privilege OS account.

Antigravity requires an explicit deep audit and security opt-in:

```sh
relay doctor --worker antigravity --deep
relay config set workers.antigravity.security_verified true
relay config enable-worker antigravity
```

### Cleanup

Relay automatically expires internal staging and workspace directories while preserving delivered result and artifact files:

- completed workspaces: 7 days
- partial workspaces: 14 days
- failed workspaces: 30 days
- cancelled workspaces: 14 days
- orphan workspaces: 7 days

```sh
relay cleanup --status
```

## Documentation

- [Agent delegation manual](manual.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Security guide](docs/SECURITY.md)
- [Cross-platform notes](docs/CROSS_PLATFORM.md)
- [Database migration notes](docs/DATABASE_MIGRATION.md)
- [Automatic cleanup policy](docs/AUTOMATIC_CLEANUP.md)
- [Release notes](RELEASE_NOTES.md)
- [Project wiki index](wiki/index.md)

<div align="center">
  <i>Built for reliable local AI delegation.</i>
</div>
