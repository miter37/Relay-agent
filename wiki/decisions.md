# Decisions

## Active

- **Relay 1.1.0 represents G5 Custom Agent Apps.** One Agent registry serves CLI, GUI, Jobs, and Schedules.
- **Custom Agent execution is shell-free.** Manifests provide argv tokens; shell operators and command substitution are rejected.
- **Enablement is audit-bound.** Executable version and runtime definition hash must match a successful deep audit.
- **GUI pre-save testing is non-persistent.** A tested definition receives a short-lived one-use token instead of creating a cancellable ghost Agent.
- **Custom Agent environments are allowlisted.** Operational variables and manifest-declared names are inherited; secret values are never stored in manifests.
- **Schedules produce ordinary Jobs.** Schedule lifecycle data remains separate while history and outputs survive Schedule deletion.

## Superseded

- **GUI Run test created the Agent before testing.** Superseded because cancelling left disabled duplicate-blocking Agent records.
- **Agent App editing rebuilt hidden fields from hardcoded defaults.** Superseded because editing could destroy model and environment configuration.
