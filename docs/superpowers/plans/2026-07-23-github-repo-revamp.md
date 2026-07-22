# GitHub Repository Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revamp the Relay GitHub repository to look professional and encourage open-source contributions.

**Architecture:** Markdown documentation updates and addition of standard `.github` community files.

**Tech Stack:** Markdown, GitHub Standards.

## Global Constraints

- No core Python code changes.
- All docs must be written in UTF-8 markdown.
- `README.md` must contain Shields.io badges.

---

### Task 1: Overhaul README.md

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: N/A
- Produces: A highly readable, professional `README.md` with badges, emojis, and clear table of contents.

- [ ] **Step 1: Overhaul README content**

Replace the current `README.md` content with the following:

```markdown
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
```

- [ ] **Step 2: Commit changes**

```bash
git add README.md
git commit -m "docs: Overhaul README with badges and standard layout"
```

---

### Task 2: Add Issue Templates

**Files:**
- Create: `.github/ISSUE_TEMPLATE/bug_report.md`
- Create: `.github/ISSUE_TEMPLATE/feature_request.md`

**Interfaces:**
- Consumes: N/A
- Produces: GitHub Issue Templates.

- [ ] **Step 1: Create Issue Template Directory**

```bash
mkdir -p .github/ISSUE_TEMPLATE
```

- [ ] **Step 2: Create `bug_report.md`**

Create `.github/ISSUE_TEMPLATE/bug_report.md` with:

```markdown
---
name: Bug report
about: Create a report to help us improve Relay
title: "[BUG] "
labels: 'bug'
assignees: ''

---

**Describe the bug**
A clear and concise description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior:
1. Run command '...'
2. With configuration '...'
3. See error

**Expected behavior**
A clear and concise description of what you expected to happen.

**Environment (please complete the following information):**
 - OS: [e.g. Windows 11, Ubuntu 22.04, macOS Sonoma]
 - Python version: [e.g. 3.11]
 - Relay version: [e.g. 0.5.0]
 - Worker CLI versions (claude, codex, agy): [e.g. claude-cli 1.0.0]

**Additional context**
Add any other context about the problem here (e.g., error logs).
```

- [ ] **Step 3: Create `feature_request.md`**

Create `.github/ISSUE_TEMPLATE/feature_request.md` with:

```markdown
---
name: Feature request
about: Suggest an idea for this project
title: "[FEATURE] "
labels: 'enhancement'
assignees: ''

---

**Is your feature request related to a problem? Please describe.**
A clear and concise description of what the problem is. Ex. I'm always frustrated when [...]

**Describe the solution you'd like**
A clear and concise description of what you want to happen.

**Describe alternatives you've considered**
A clear and concise description of any alternative solutions or features you've considered.

**Additional context**
Add any other context or screenshots about the feature request here.
```

- [ ] **Step 4: Commit changes**

```bash
git add .github/ISSUE_TEMPLATE/
git commit -m "chore: Add GitHub Issue templates"
```

---

### Task 3: Add Pull Request Template

**Files:**
- Create: `.github/PULL_REQUEST_TEMPLATE.md`

**Interfaces:**
- Consumes: N/A
- Produces: GitHub PR Template.

- [ ] **Step 1: Create `PULL_REQUEST_TEMPLATE.md`**

Create `.github/PULL_REQUEST_TEMPLATE.md` with:

```markdown
## Description
<!-- Please include a summary of the change and which issue is fixed. -->
Fixes # (issue)

## Type of change
<!-- Please delete options that are not relevant. -->
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] This change requires a documentation update

## Checklist:
- [ ] I have performed a self-review of my own code
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have run tests locally (e.g., `python -m unittest`) and they pass
```

- [ ] **Step 2: Commit changes**

```bash
git add .github/PULL_REQUEST_TEMPLATE.md
git commit -m "chore: Add GitHub PR template"
```

---

### Task 4: Add CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

**Interfaces:**
- Consumes: N/A
- Produces: Community contribution guidelines.

- [ ] **Step 1: Create `CONTRIBUTING.md`**

Create `CONTRIBUTING.md` in the root directory with:

```markdown
# Contributing to Relay

First off, thank you for considering contributing to Relay! We welcome PRs, bug reports, and suggestions.

## Development Setup

1. **Clone the repository:**
   ```sh
   git clone https://github.com/miter37/Relay.git
   cd Relay
   ```
2. **Ensure Python 3.11+ is installed.**
3. **Run local tests:**
   Relay includes mock tests that do not call real APIs.
   ```sh
   PATH="$PWD/mocks:$PATH" PYTHONPATH=. python -m unittest tests.test_relay.RelayTests -v
   ```

## Pull Request Process

1. Create a new branch from `master` (`git checkout -b feature/your-feature-name`).
2. Implement your changes.
3. Add or update tests as necessary.
4. Ensure all tests pass.
5. Submit a PR and fill out the provided template.

## Code Style

- Keep code simple and explicit.
- Follow PEP 8 guidelines where possible.
- Avoid introducing heavy third-party dependencies; Relay aims to use the Python standard library as much as possible.
```

- [ ] **Step 2: Commit changes**

```bash
git add CONTRIBUTING.md
git commit -m "docs: Add CONTRIBUTING.md"
```

---

### Task 5: Add CODE_OF_CONDUCT.md

**Files:**
- Create: `CODE_OF_CONDUCT.md`

**Interfaces:**
- Consumes: N/A
- Produces: Community code of conduct.

- [ ] **Step 1: Create `CODE_OF_CONDUCT.md`**

Create `CODE_OF_CONDUCT.md` in the root directory with:

```markdown
# Contributor Covenant Code of Conduct

## Our Pledge

We as members, contributors, and leaders pledge to make participation in our
community a harassment-free experience for everyone, regardless of age, body
size, visible or invisible disability, ethnicity, sex characteristics, gender
identity and expression, level of experience, education, socio-economic status,
nationality, personal appearance, race, religion, or sexual identity
and orientation.

We pledge to act and interact in ways that contribute to an open, welcoming,
diverse, inclusive, and healthy community.

## Our Standards

Examples of behavior that contributes to a positive environment for our
community include:

* Demonstrating empathy and kindness toward other people
* Being respectful of differing opinions, viewpoints, and experiences
* Giving and gracefully accepting constructive feedback
* Accepting responsibility and apologizing to those affected by our mistakes,
  and learning from the experience
* Focusing on what is best not just for us as individuals, but for the
  overall community

Examples of unacceptable behavior include:

* The use of sexualized language or imagery, and sexual attention or
  advances of any kind
* Trolling, insulting or derogatory comments, and personal or political attacks
* Public or private harassment
* Publishing others' private information, such as a physical or email
  address, without their explicit permission
* Other conduct which could reasonably be considered inappropriate in a
  professional setting
```

- [ ] **Step 2: Commit changes**

```bash
git add CODE_OF_CONDUCT.md
git commit -m "docs: Add CODE_OF_CONDUCT.md"
```
