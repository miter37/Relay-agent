# Decisions

## Active

- **Relay 1.1.0 represents G5 Custom Agent Apps.** One Agent registry serves CLI, GUI, Jobs, and Schedules.
- **Custom Agent execution is shell-free.** Manifests provide argv tokens; shell operators and command substitution are rejected.
- **Enablement is audit-bound.** Executable version and runtime definition hash must match a successful deep audit.
- **GUI pre-save testing is non-persistent.** A tested definition receives a short-lived one-use token instead of creating a cancellable ghost Agent.
- **Custom Agent environments are allowlisted.** Operational variables and manifest-declared names are inherited; secret values are never stored in manifests.
- **Schedules produce ordinary Jobs.** Schedule lifecycle data remains separate while history and outputs survive Schedule deletion.
- **Schedule eligibility is separate from schedule permission.** A successful replayable Job may open the Schedule editor; saving still requires service-isolation acknowledgement.
- **GUI health is user-triggered.** Health is checked at startup and by an explicit refresh action, not on a continuous timer.
- **Finished history is hierarchical.** The GUI uses a collapsible Finished/date/task tree; task names and result states are separate columns.
- **Task-entry safety defaults are explicit.** GUI-created Jobs default fallback, force-new, and overwrite to enabled, with inline help explaining the consequences.
- **Unified Full Access Mode:** Workers support a unified `full_access_mode` flag which toggles their respective security bypasses (e.g., YOLO, skip permissions). GUI and CLI read the same daemon/config state; a running daemon is updated through `/v1/security/full-access/{worker}`. When disabled, sandbox/permission errors return specific guidance advising the user about this setting.
- **Windows worker consoles stay hidden:** GUI, daemon health probes, model discovery, and job workers launch child processes with `CREATE_NO_WINDOW`; job output remains in Relay logs and files.
- **Working folders and artifacts are distinct.** Interactive Jobs apply a verified isolated delta to `target_path` and copy changed/created files to `artifact_path`; result files retain their existing meaning.
- **Progress checks are observational and manual.** They never message or signal the Agent; structured results are events shown in the Logs tab without modifying Agent stdout/stderr.

## Superseded

- **GUI Run test created the Agent before testing.** Superseded because cancelling left disabled duplicate-blocking Agent records.
- **Agent App editing rebuilt hidden fields from hardcoded defaults.** Superseded because editing could destroy model and environment configuration.
