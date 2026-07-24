# Overview

Relay-agent is a Python 3.11+ delegation broker for Claude Code, Codex CLI, Antigravity, and manifest-backed custom Agent Apps. It provides a CLI, authenticated local daemon API, SQLite-backed Job history, daemon-managed Schedules, and an optional PySide6 desktop GUI.

The current development branch is `feat/g5-custom-agent-apps` at Relay 1.1.0. G5 adds Custom Agent Apps shared by CLI, GUI, normal Jobs, and Schedules. The branch is represented by Draft PR #14 and its current CI is green across the supported OS/Python matrix.

Relay Home owns runtime configuration, Agent App manifests, audit specs, history, workspaces, logs, results, and artifacts. Repository source and tests define behavior; this wiki records only the current working understanding.
