# Project model

## Actors and entry points

- Humans and external agents submit work through `relay`, the authenticated daemon API, or the GUI.
- The daemon owns Job queues plus Schedule, Project, and Routine reconciliation loops.
- `RelayEngine` resolves Agent definitions, enforces readiness and service isolation, snapshots inputs, supervises workers, validates results, and records history.
- Internal Project/Routine execution records `caller=service`; `trigger_type` explains why a Run exists and `submitted_via` records its entry surface.

## Durable objects

| Product object | Persistence and rule |
|---|---|
| Task Run / Run | Existing `jobs` row; `run_id` aliases immutable `job_id` for compatibility. |
| Task | Versioned mutable definition in `tasks`; every Run keeps an immutable Task snapshot. |
| Attempt | `attempts` child row with the actual Worker and audit-bound execution metadata. |
| Artifact | Immutable UID, role, SHA-256, producer, and Relay-managed file. |
| Lineage | `artifact_lineage` records source Artifact, consumer Run, alias, and verified snapshot hashes. |
| Project | Versioned DAG definition containing Task nodes, Artifact-to-input connections, and final-output selection. |
| Project Run | Persistent state machine with immutable Project/Task snapshots and child Task Runs. |
| Routine | Timezone-aware recurring Task/Project target with overlap, missed-run, version, input, and notification policies. |
| Approval | Persistent checkpoint decision; human edits are new `producer=human` Artifacts linked to the original. |

## Execution flow

```text
caller → Task Run → Attempt(s) → Artifact(s)
                    ↑              ↓ immutable UID + hash snapshot
Project/Routine ────┘        next Task Run input manifest + lineage
```

Project connections are resolved strictly by `(source node, Artifact role)` and passed into child Task Runs as A1/A2-style Artifact inputs. The engine copies each input into Relay Home, verifies size and SHA-256, and records consumer lineage before a Worker receives it. Missing or ambiguous selected final Artifacts fail the Project Run.

Routine ticks execute only due occurrences. Atomic occurrence claims prevent duplicate dispatch; pinned-version mismatches fail instead of silently running a newer Task/Project. Existing Schedules remain independent and continue producing ordinary linked Jobs.

Checkpoint nodes pause in `awaiting_approval`. Approval resumes descendants, rejection fails the run, and approved human edits take precedence for downstream role resolution. Folder delivery is restricted to configured `allowed_delivery_roots` at both definition and delivery time.

FTS5 indexes are derived and rebuildable. Semantic search currently uses the pluggable embedding interface and explicitly falls back to FTS5 when no backend is configured. Quality attention covers Task and Project Runs. Export archives are deterministic, hash-manifested, redact notification secrets and local Artifact paths, and optionally round-trip Task Runs, Artifacts, and lineage.
