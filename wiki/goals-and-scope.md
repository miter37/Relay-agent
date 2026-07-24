# Goals and scope

## Goals

- Reliably delegate work to supported AI CLIs and return validated JSON or text results.
- Preserve Job history, outputs, artifacts, and replay/scheduling relationships across upgrades.
- Keep CLI, daemon API, GUI, and Schedules on one Agent registry and compatibility contract.
- Require deep capability verification before an Agent can execute enabled work.
- Behave consistently on Windows, Linux, and macOS.

## Constraints

- Relay validates execution and delivery contracts, not the factual quality of AI output.
- Relay workspaces reduce collisions but are not an OS security sandbox.
- Unattended execution requires operator-managed account isolation and explicit acknowledgement.
- Real built-in provider CLIs are field-validated on Windows; Linux/macOS CI uses mocks.

## Non-goals

- Replacing provider CLIs or their authentication.
- Guaranteeing model answer correctness.
- Treating process-level workspace isolation as a complete security boundary.
- Starting G6 packaging work before the current G5 branch is accepted.
