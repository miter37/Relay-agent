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

> **Note on Guarantees**: Relay validates the execution and delivery contract (result-file creation, encoding, schema, artifact paths, and process completion). It **does not** verify the factual accuracy or reasoning quality of the AI-generated content.

> **Note on Cross-platform**: Cross-platform code paths are implemented, but provider-specific behavior must be validated on the target machine with `relay doctor --deep`. Some Windows and macOS operational paths still require field validation.

## 📑 Table of Contents
- [✨ Key Features](#-key-features)
- [📦 System Requirements](#-system-requirements)
- [🚀 Installation & Verification](#-installation--verification)
- [💡 Usage & Task Files](#-usage--task-files)
- [🤖 Hermes AI & Multi-Worker Delegation](#-hermes-ai--multi-worker-delegation)
- [🔍 Model Discovery & Limitations](#-model-discovery--limitations)
- [📄 JSON Result Contract](#-json-result-contract)
- [⚙️ Configuration & Security](#-configuration--security)
- [🧹 Cleanup and Retention](#-cleanup-and-retention)
- [📚 Documentation](#-documentation)

---

## ✨ Key Features

Relay is a reliable task broker designed to connect your always-on AI agents with powerful coding CLIs.

- 🤖 **3 Major AI CLIs Supported**: Natively supports task delegation to `Claude Code`, `Codex CLI`, and `Antigravity`.
- 🤝 **Perfect for Agent Delegation**: Always-on AI agents (like Hermes or OpenClaw) can hand off complex, long-running tasks to Relay and retrieve the final results asynchronously.
- 📂 **Dedicated Workspaces**: Each job runs from a separate Relay-managed workspace. This reduces accidental file collisions but is not a complete OS sandbox. Unattended use requires a dedicated low-privilege OS account.
- 🗄️ **Persistent History**: Every delegated job's history, errors, and output paths are meticulously recorded in a local SQLite database.
- ✅ **Validated Delivery Contract**: Relay checks result-file creation, encoding, JSON/TXT structure, artifact paths, and process completion before publishing outputs.

---

## 📦 System Requirements

- Windows 11, Linux, or macOS
- Python 3.11+
- Installed and logged-in AI CLI workers (`claude`, `codex`, `agy`)
- *For Hermes Unattended Execution:* A dedicated low-privilege OS account is strictly required.

---

## 🚀 Installation & Verification

### 1. Clone & Install
```sh
git clone https://github.com/miter37/Relay.git
cd Relay
```

**Windows (PowerShell):**
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_windows.ps1
```

**Linux / macOS:**
```sh
chmod +x scripts/install_unix.sh
./scripts/install_unix.sh
```

### 2. Verify Workers
```sh
relay init
relay doctor --worker claude --deep
relay doctor --worker codex --deep
```

### 3. Antigravity Setup (Optional & Advanced)
Because Antigravity has strong permission bypass capabilities, it requires explicit opt-in after you have verified your OS-level isolation:
```sh
relay doctor --worker antigravity --deep
# ONLY run these if you have configured OS-level isolation:
relay config set workers.antigravity.security_verified true
relay config enable-worker antigravity
```

---

## 💡 Usage & Task Files

### Short Requests (Inline)
Use direct task arguments for simple, short requests:
```sh
relay "Investigate today's major AI semiconductor news" --worker codex
```
*Note on fallback*: `--worker codex` means "Try Codex first, but fallback if it fails." If you want *only* Codex, use `--worker codex --no-fallback`.

### Structured Requests (Task Files)
For long or structured tasks (recommended for agents), use a UTF-8 Markdown task file:
```sh
relay run `
  --task-file "D:\RelayInput\request.md" `
  --worker claude `
  --format json `
  --machine
```

---

## 🤖 Hermes AI & Multi-Worker Delegation

By registering `skills/hermes-relay/SKILL.md` in your AI environment, **Hermes AI** can use Relay to delegate complex tasks and aggregate results.

**Example:** "Ask agy, codex, and claude who the next US president will be, and aggregate the 200-word reasoning from each."

To achieve this, the agent submits 3 separate asynchronous jobs:
```sh
relay submit --task-file "task.md" --worker claude --request-id "q-claude" --machine
relay submit --task-file "task.md" --worker codex --request-id "q-codex" --machine
relay submit --task-file "task.md" --worker antigravity --request-id "q-agy" --machine
```

**Asynchronous Flow:**
1. Parse the `job_id` from the `submit` JSON receipt.
2. Wait for completion: `relay wait <job_id> --machine`
3. Get final receipt: `relay result <job_id> --machine`
4. Read the actual output file from the `result_path` provided in the receipt.

---

## 🔍 Model Discovery & Limitations

Relay discovers models using worker-specific methods. Codex and Antigravity can provide account-aware catalogs when supported. Claude Code does not expose a complete non-interactive model-list API, so its results may include configured or known model candidates rather than a definitive account-level list.

```sh
relay models
relay models --worker codex --refresh
```

**Checking Model Availability (`model-check`):**
Check whether a model is listed or can be minimally verified. Claude uses a small inference probe, while Codex and Antigravity currently check catalog membership.
```sh
relay model-check --worker claude --model sonnet --machine
```

---

## 📄 JSON Result Contract

When requesting `--format json`, the file written to your `--out` path will follow this structure:

```json
{
  "schema_version": "1.0",
  "status": "complete",
  "answer": "The requested analysis...",
  "sources": ["https://example.com"],
  "uncertainties": ["Market volatility makes this unpredictable"],
  "missing_items": [],
  "artifacts": ["path/to/chart.csv"]
}
```
*Important*: The Relay receipt status (e.g., `completed`) indicates successful CLI execution. The internal JSON `status` (e.g., `complete` or `partial`) indicates the AI's self-reported success on the actual task logic.

---

## ⚙️ Configuration & Security

**Security & Unattended Execution:**
To use `--caller hermes`, the operator must configure a low-privilege account and ACL isolation. After securing the host, acknowledge it:
```sh
relay security --machine
relay config set service_isolation_acknowledged true
```

**Set default workers and fallbacks:**
```sh
relay config set default_worker claude
relay config set fallback_order codex,antigravity
```

---

## 🧹 Cleanup and Retention

Relay's daemon automatically deletes expired staging and workspace directories based on the job status:
- **Completed**: 7 days
- **Partial**: 14 days
- **Failed**: 30 days
- **Cancelled**: 14 days
- **Orphan workspaces**: 7 days

*Note: Automated cleanup applies to Relay's internal workspaces. Final output files and artifacts delivered to your `--out` paths are never automatically deleted.*

```sh
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
