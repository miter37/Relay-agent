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

## Superseded

- **GUI Run test created the Agent before testing.** Superseded because cancelling left disabled duplicate-blocking Agent records.
- **Agent App editing rebuilt hidden fields from hardcoded defaults.** Superseded because editing could destroy model and environment configuration.
