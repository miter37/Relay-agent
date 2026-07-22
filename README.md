<div align="center">
  <h1>🚀 Relay</h1>
  <p><strong>A reliable delegation broker for AI CLIs (Claude, Codex, Antigravity) on Windows, Linux, and macOS.</strong></p>

  <p>
    <a href="https://github.com/miter37/Relay/releases"><img src="https://img.shields.io/github/v/release/miter37/Relay?style=flat-square" alt="Release"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square" alt="Python"></a>
    <a href="https://github.com/miter37/Relay/blob/master/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License"></a>
    <a href="https://github.com/miter37/Relay/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square" alt="PRs Welcome"></a>
    <img src="https://img.shields.io/badge/OS-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey?style=flat-square" alt="OS">
  </p>
</div>

## 📑 Table of Contents
- [✨ Key Guarantees](#-key-guarantees)
- [📦 System Requirements](#-system-requirements)
- [🚀 Quick Start (Installation)](#-quick-start-installation)
- [💡 Usage Examples](#-usage-examples)
- [🤖 Hermes & Background Tasks](#-hermes--background-tasks)
- [🔍 Model Discovery](#-model-discovery)
- [⚙️ Configuration & Cleanup](#-configuration--cleanup)
- [📚 Documentation](#-documentation)

---

## ✨ Key Features

Relay is a reliable task broker designed to connect your always-on AI agents with powerful coding CLIs.

- 🤖 **3 Major AI CLIs Supported**: Natively supports task delegation to `Claude Code`, `Codex CLI`, and `Antigravity`.
- 🤝 **Perfect for Agent Delegation**: Always-on AI agents (like Hermes or OpenClaw) can hand off complex, long-running tasks to Relay and simply retrieve the final results later.
- 📂 **Isolated Environments**: All AI execution happens in dedicated, isolated temporary workspaces to prevent accidental modification of your critical project files.
- 🗄️ **Persistent History & Artifacts**: Every delegated job's history, errors, and output artifacts are meticulously recorded in a local SQLite database for easy tracking and retrieval.
- ✅ **Guaranteed Delivery**: Validates that the output is in the expected JSON/TXT format before delivering it back to the requesting agent.

---

## 📦 System Requirements

- Windows 11, Linux, or macOS
- Python 3.11+
- Installed and logged-in AI CLI workers (`claude`, `codex`, `agy`)
- *For Hermes Unattended Execution:* A dedicated low-privilege OS account is required.

---

## 🚀 Quick Start (Installation)

### Windows (PowerShell)
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1

relay init
relay doctor --worker claude --deep
relay doctor --worker codex --deep
```

### Linux / macOS
```sh
chmod +x scripts/install_unix.sh
./scripts/install_unix.sh

relay init
relay doctor --worker claude --deep
relay doctor --worker codex --deep
```

---

## 💡 Usage Examples

### Basic Synchronous Run
```powershell
relay "Investigate today's major AI semiconductor news" --worker codex
```

### Specifying Output Paths
```powershell
relay "Research CSP CAPEX" `
  --worker claude `
  --format json `
  --out "D:\Research\csp-capex.json" `
  --artifacts "D:\Research\csp-capex-artifacts"
```

### Text Format with Attachments
```powershell
relay "Summarize this document" --format txt --attach "D:\Input\report.pdf"
```

---

## 🤖 Hermes AI & Agent Delegation

By registering `skills/hermes-relay/SKILL.md` in your AI environment, **Hermes AI** can use Relay to delegate complex tasks to the 3 main AI CLIs (Antigravity, Codex, Claude Code) and aggregate their results.

**Example Delegation Request:**
> "Hermes: agy, codex, claude code 에게 다음의 내용을 모두 물어보고 답변을 취합해 알려줘.
> '다음 미국 대통령은 누가 될 것 같은가, 인물을 한명 답하고 그 근거를 200자로 적어서 답할 것'."

Under the hood, Hermes will use Relay's daemon to submit long-running jobs and track them asynchronously.

```powershell
relay submit `
  --task-file "D:\Hermes\relay-input\president-task.md" `
  --format json `
  --out "D:\Hermes\relay-results\result.json" `
  --caller hermes `
  --machine
```
Track and retrieve:
```powershell
relay wait <job_id> --machine
relay result <job_id> --machine
```

---

## 🔍 Model Discovery

Relay can dynamically query and cache the models available to each worker.

```powershell
relay models
relay models --worker codex --refresh
relay models --worker claude --machine
```
Check if a specific model is usable:
```powershell
relay model-check --worker claude --model claude-3-5-sonnet-20241022 --machine
```

---

## ⚙️ Configuration & Cleanup

**Set default workers and fallbacks:**
```powershell
relay config set default_worker claude
relay config set fallback_order codex,antigravity
```

**Automatic Cleanup:**
Relay automatically deletes expired staging and workspace directories based on status (7 days for completed, 30 for failed).
```powershell
relay cleanup --status
```

---

## 📚 Documentation

For deeper details, consult the `docs/` folder:
- [Development Plan](docs/DEVELOPMENT_PLAN.md)
- [Capability Audit](docs/CAPABILITY_AUDIT.md)
- [Security Guidelines](docs/SECURITY.md)
- [Cross-Platform Notes](docs/CROSS_PLATFORM.md)
- [Automatic Cleanup Policy](docs/AUTOMATIC_CLEANUP.md)

<div align="center">
  <i>Built with ❤️ for reliable AI delegation.</i>
</div>
