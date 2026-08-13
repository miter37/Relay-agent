# Overview

Relay is a Python 3.11+ delegation broker for Claude Code, Codex CLI, Antigravity, and manifest-backed custom Agent Apps. It provides a CLI, authenticated local daemon API, SQLite history, daemon-managed Schedules and Routines, and an optional PySide6 desktop GUI.

The current development branch is `feat/unified-artifact-explorer` at Relay 1.1.0. Product-direction Phases 0–6 are implemented in the CLI/daemon core: Task Run compatibility, immutable Artifact handoff and lineage, FTS5 search, registered Tasks, persistent Project DAG runs, Routines, approvals, result review gates, comparison, quality/attention, notifications, and export/import. GUI surfaces for Tasks, Projects, Routines, Project Runs, and the Reviews Inbox are implemented. Agents can now follow the documented Catalog-first discovery and Artifact reuse workflow.

The Phase 0–6 review raised the database schema to v17 and corrected legacy migration completeness, Task/Project catalog summary persistence, Project Artifact handoff, service caller identity, Routine due-time/version/recovery behavior, approval and review concurrency, checkpoint delivery allow-list enforcement, final-output failure semantics, notification enforcement/retry, quality thresholds, Project attention coverage, and archive integrity/secret handling. Read-only Task, Task Run, Project, and Project Run catalogs are available for Agent-driven candidate selection; ranking remains outside Relay.

Relay Home owns runtime configuration, Agent App manifests, audit specs, history, workspaces, logs, results, input snapshots, artifacts, and lineage. Repository code and tests are authoritative; this wiki records the current working understanding.

Task Interface v1 is now an additive contract layer. Parameters remain the existing `input_schema`; named Artifact inputs and Outputs are normalized from `output_contract`, while every Task exposes Relay's system `result` Output. Legacy Tasks and A1/A2 Project bindings remain runnable. New named connections are checked centrally before Project save, and Task output contracts are checked again after Artifact scan before Review/publication.

Projects can optionally contain named `from_output`/`to_input` connections, durable `wait` nodes (manual or timed), and an allow-listed final `delivery` folder. Wait state is persisted in Project Run steps and resumes safely after daemon restart; final selected Outputs are copied under the Project Run ID only after all review gates complete.

The GUI global navigation now lives in a compact, scrollable top bar; the former left navigation rail is removed so each screen's own list/detail workspace can use the full window width. The Relay-adjacent current-section title and routine refresh notices are suppressed; active navigation is shown by the selected top-menu treatment, while actionable errors remain in the status area.
