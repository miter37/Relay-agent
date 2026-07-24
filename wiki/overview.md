# Overview

Relay-agent is a Python 3.11+ delegation broker for Claude Code, Codex CLI, Antigravity, and manifest-backed custom Agent Apps. It provides a CLI, authenticated local daemon API, SQLite-backed Job history, daemon-managed Schedules, and an optional PySide6 desktop GUI.

The current development branch is `feat/g5-custom-agent-apps` at Relay 1.1.0. G5 adds Custom Agent Apps shared by CLI, GUI, normal Jobs, and Schedules. Draft PR #14 is still the integration point; the latest GUI fixes are locally verified but must not be described as CI-verified until CI is explicitly checked.

The GUI performs health checks at startup and on user request. The daemon reports verification health for every enabled Agent; an unhealthy engine is named in the header instead of being presented as an overall healthy state. Completed replayable Jobs can open the Schedule editor, while schedule creation still requires service-isolation acknowledgement.

Relay Home owns runtime configuration, Agent App manifests, audit specs, history, workspaces, logs, results, and artifacts. Repository source and tests define behavior; this wiki records only the current working understanding.
