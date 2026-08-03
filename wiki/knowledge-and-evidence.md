# Knowledge and evidence

## Verified facts

- Relay version remains 1.1.0; daemon API schema revision remains 5 with minimum GUI 1.1.0. Source: current code and tests.
- Current SQLite schema is v12. Legacy v0 databases now receive the complete additive migration chain through Tasks, Projects, Routines, approvals, notifications, receipt versioning, and Artifact producer metadata. Source: `relay/db.py` and migration fixtures.
- Project Artifact connections become real child Run input manifests and lineage rows, not display-only metadata. Source: Project runtime acceptance regression.
- Project and Routine child Runs use `caller=service` and therefore enforce service-isolation acknowledgement. Source: engine/runtime tests.
- Routine ticks do not dispatch future occurrences; pinned mismatches fail with `ROUTINE_VERSION_PIN_INVALID`. Source: Routine runtime regressions.
- Pending checkpoint creation is atomic per Project step; human edits are producer=human Artifacts with original-draft lineage. Source: approval concurrency and edit tests.
- Checkpoint delivery paths are validated against `allowed_delivery_roots` when Projects are created/updated and again immediately before copy-out. Source: Phase 6a path-boundary tests.
- Routine failure policies invoke webhook notifications, retry up to the configured attempt count, and log every attempt. Source: Phase 6d service tests.
- Project failure notification policies and Project Runs are included in quality/attention views. Source: Phase 6c/6d regression tests.
- Export manifests carry per-entry SHA-256 values, redact webhook secrets, and optional import restores Task Runs and Artifact bytes under constrained paths. Source: Phase 6e round-trip/security tests.
- Import rename conflicts rewrite Task IDs inside Project definitions and Project IDs inside Routine targets. Source: lifecycle reference-integrity regression.
- The full local suite has 398 tests; the final verification also runs Ruff, format check, compileall, and the release builder. CI status is not inferred from local results.

## Current uncertainty and deferred scope

- GUI management surfaces for Phase 3–6 domain objects are deferred; the CLI and daemon API are the complete interfaces today.
- No concrete embedding provider ships with Relay; semantic queries use the documented FTS5 fallback unless an operator supplies one.
- Export/import currently focuses Run restoration on Task Runs and their Artifacts/lineage; full Project/Routine operational-history restoration remains follow-up work.
- The Phase 0–6 branch `feat/phase0-domain-compat` is pushed to origin at the reviewed commit; no main merge, PR, or release cut is implied.
