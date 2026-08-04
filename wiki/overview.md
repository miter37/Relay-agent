# Overview

Relay is a Python 3.11+ delegation broker for Claude Code, Codex CLI, Antigravity, and manifest-backed custom Agent Apps. It provides a CLI, authenticated local daemon API, SQLite history, daemon-managed Schedules and Routines, and an optional PySide6 desktop GUI.

The current development branch is `feat/phase0-domain-compat` at Relay 1.1.0. Product-direction Phases 0–6 are implemented in the CLI/daemon core: Task Run compatibility, immutable Artifact handoff and lineage, FTS5 search, registered Tasks, persistent Project DAG runs, Routines, approvals, comparison, quality/attention, notifications, and export/import. GUI surfaces for Tasks, Projects, and Routines are implemented; approvals and dashboards remain deferred. Agents can now follow the documented Catalog-first discovery and Artifact reuse workflow.

The Phase 0–6 review raised the database schema to v14 and corrected legacy migration completeness, Task/Project catalog summary persistence, Project Artifact handoff, service caller identity, Routine due-time/version/recovery behavior, approval concurrency and human-edit lineage, checkpoint delivery allow-list enforcement, final-output failure semantics, notification enforcement/retry, quality thresholds, Project attention coverage, and archive integrity/secret handling. Read-only Task, Task Run, Project, and Project Run catalogs are available for Agent-driven candidate selection; ranking remains outside Relay.

Relay Home owns runtime configuration, Agent App manifests, audit specs, history, workspaces, logs, results, input snapshots, artifacts, and lineage. Repository code and tests are authoritative; this wiki records the current working understanding.
