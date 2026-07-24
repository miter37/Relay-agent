# Knowledge and evidence

## Verified facts

- Relay version is 1.1.0 and the daemon health contract reports API schema revision 5 with minimum GUI version 1.1.0. Source: current code and tests.
- G5 Agent Apps use normalized JSON manifests, argv execution without shell reparsing, definition-bound deep audits, and recoverable deletion. Source: `relay/agent_apps.py`, adapters, and G5 tests.
- Pre-save GUI tests do not persist an Agent App; successful definitions receive an expiring one-use token. Source: current Agent App service and GUI tests.
- Custom manifest subprocesses inherit operational and explicitly declared environment variables; built-in and legacy behavior remains compatible. Source: adapter and supervisor tests.
- CI checks Ruff, release building, the full unit suite on three OSes and three Python versions, plus GUI smoke on all three OSes. Source: `.github/workflows/ci.yml`.

## Current uncertainty

- Draft PR #14 is not yet merged, so G5 remains development-branch truth rather than the released `master` baseline.
- Real provider CLI behavior on Linux and macOS is not field-validated by CI.
- README's `relay add-agent` section still primarily describes the legacy registration path and needs reconciliation with Agent Apps.
