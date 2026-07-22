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

## ✨ Key Guarantees

Relay delegates one-off tasks to AI CLIs safely and returns JSON/TXT outputs predictably.

- 🛡️ **Safe Probing**: Audits CLI installation (`doctor --deep`) before execution.
- 📂 **Workspace Isolation**: AI runs in an isolated workspace, not in your final directories.
- ✅ **Atomic Delivery**: Validates existence, UTF-8, and JSON schema before moving results.
- 🗄️ **SQLite History**: Logs jobs, attempts, errors, and artifact hashes.
- ⚡ **Graceful Fallback**: Automatically falls back to a different worker on technical failure.

*Note: Relay guarantees delivery, format, and execution contracts, NOT the factual correctness of the AI's output.*

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

## 🤖 Hermes & Background Tasks

Relay excels at handling long-running, asynchronous tasks via its daemon.

```powershell
relay config set service_isolation_acknowledged true

relay submit `
  --task-file "D:\Hermes\relay-input\telegram-8821.md" `
  --format json `
  --out "D:\Hermes\relay-results\telegram-8821.json" `
  --artifacts "D:\Hermes\relay-artifacts\telegram-8821" `
  --request-id "telegram-chat123-message8821" `
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
