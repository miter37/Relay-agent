# Unified Artifact Explorer Design

## Purpose

Relay must present generated evidence consistently wherever a human inspects work. A completed Task Run, a Project Run node, and a pending Review currently expose the same durable Artifact records through three different interfaces: raw JSON in Task Run `Files`, a partial format-aware Project Run viewer, and a text-only Review summary. This design replaces those divergent presentations with one reusable, read-only Artifact Explorer.

## Product Decision

The result file is an Artifact with `role=result`; it is not a separate user-facing file concept. Task Run `Result` and `Files` tabs become one `Artifacts` tab. The `Answer` tab remains because it is the human-readable answer extracted from the result contract. The result Artifact remains fully inspectable in structured and raw form inside `Artifacts`.

The same Explorer is embedded in:

- Task Run detail: result Artifact first, followed by all produced Artifacts.
- Project Run detail: final Artifacts first, followed by node-grouped Artifacts, including current review candidates.
- Reviews Inbox: current review-round candidate Artifacts, with the result Artifact selected automatically.

## Architecture

### Shared GUI component

Create `relay/gui/artifacts.py` and move the current Project Run preview responsibilities out of `relay/gui/project_runs.py` into:

- `ArtifactRecord`: normalized immutable metadata used by the GUI.
- `ArtifactGroup`: a labeled collection of records.
- `artifact_kind(record)`: MIME-first, extension-second preview classification.
- `merge_artifact_records(...)`: stable UID-based deduplication.
- `ArtifactExplorerView`: grouped list, metadata header, path actions, preview stack, selection and lazy-content cache.

The component owns presentation only. It never performs daemon requests and never mutates Artifact files. It emits signals when metadata/content or an OS-level file action is needed.

### Context adapters

Task Run, Project Run, and Review widgets adapt their existing payloads into `ArtifactGroup` values. They retain ownership of screen-specific grouping, selection policy, and stale-response checks.

- Task Run groups: `Result`, then `Artifacts`.
- Project Run groups: `Final Artifacts`, then one group per Project node in project-definition order.
- Review groups: `Current candidate`, with `Candidate · Not published` presentation.

### Existing APIs stay authoritative

Published Artifact metadata and text content continue through:

- `GET /v1/artifacts/{artifact_uid}`
- `GET /v1/artifacts/{artifact_uid}/content?max_bytes=262144`

`GET /v1/jobs/{task_run_id}/artifacts` remains published-only so candidate files cannot enter normal reuse and search flows.

### Review-scoped candidate access

Add one endpoint:

```text
GET /v1/reviews/{review_id}/artifacts/{artifact_uid}/content?max_bytes=262144
```

`ReviewService.artifact_content(review_id, artifact_uid, max_bytes)` must verify all of the following before reading:

1. The Review exists and has a current round.
2. The Artifact exists.
3. The Artifact's `job_id` equals the current round's `task_run_id`.
4. The Artifact publication status is `candidate`, `published`, or `rejected`.
5. The DB-backed `final_path` resolves to the file read by `Database.artifact_content`.

This endpoint does not make a candidate reusable, searchable, or visible through generic Artifact routes. The Review detail response already carries candidate metadata; the endpoint only supplies bounded text content.

## Information Architecture

### Task Run

```text
Overview | Task | Inputs | Progress | Answer | Artifacts | Logs | Events
```

On a completed, partial, or review-pending Task Run, entering `Artifacts` automatically selects the result Artifact when available. The tab remains usable when only non-result Artifacts exist. `Answer` still requests and renders the result contract's answer field.

### Project Run

```text
Pipeline | Artifacts | Timeline | Orchestrator
```

The existing tab order stays unchanged. Pipeline Artifact activation switches to `Artifacts` and selects the matching UID. When no explicit selection exists, the first final Artifact is selected after metadata is available. Published and candidate records with the same UID are deduplicated.

### Reviews Inbox

The selected Review shows status, round, reviewer, guidelines, Task Run identity, and the shared Explorer above the feedback/actions area. The current round's result Artifact is selected automatically. Candidate status is visible next to the role and filename. Confirm, rerun, and reject actions remain disabled unless the Review is actionable.

After a review action, the Reviews list and selected Project/Task Run must refresh so the same UID receives its new publication status and final path.

## Artifact Metadata and Actions

Every selected Artifact shows:

- Role.
- Filename.
- Relative path.
- Full local path.
- MIME type.
- Size.
- SHA-256.
- Publication status.
- Producer Task Run.
- Project node when known.

Actions are consistent across all embeddings:

- `Open file`: enabled only for an existing file.
- `Open containing folder`: enabled only when the parent directory exists.
- `Copy path`: enabled when a non-empty full path is available.

The full path is selectable text. OS opening stays in `MainWindow._open_path`; the Explorer emits paths instead of invoking external applications itself.

## Preview Matrix

| Kind | Extensions / MIME | In-app behavior |
| --- | --- | --- |
| JSON | `.json`, `application/json`, `+json` | Expandable structure and `Raw` sub-tab. |
| JSON lines | `.jsonl`, `.ndjson` | Bounded row list; malformed rows remain visible as text. |
| Table | `.csv`, `.tsv` | Read-only table capped at 500 rows and 100 columns. |
| Markdown | `.md`, `.markdown` | Rendered Markdown and `Raw` sub-tab. |
| Safe HTML | `.html`, `.htm`, `text/html` | Qt rich-text rendering with scripts, embedded objects, and external/local resource loading disabled; `Raw` remains available. |
| Text/code | `.txt`, `.log`, `.yaml`, `.yml`, `.xml`, `.toml`, `.ini`, `.cfg`, `.conf`, `.rst`, `.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.css`, `.scss`, `.sql`, `.sh`, `.bash`, `.zsh`, `.bat`, `.cmd`, `.ps1`, `.diff`, `.patch` | Plain text with truncation notice. |
| Raster image | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp` | Aspect-preserving preview; reject images above 40 million decoded pixels and fall back to metadata/external open. |
| SVG | `.svg`, `image/svg+xml` | Render with Qt SVG without following external resources. |
| PDF | `.pdf`, `application/pdf` | Native `QPdfDocument`/`QPdfView`; files over 100 MiB fall back to metadata/external open. |
| Archive | `.zip`, `.tar`, `.tgz`, `.tar.gz` | List at most 2,000 member names and sizes; never extract. |
| Office/media/binary | DOCX, XLSX, PPTX, audio, video, executables, unknown | Metadata and OS-level open actions only. |

Text content is bounded to 262,144 bytes. A visible banner states when content, rows, columns, archive members, or image/PDF size limits truncate or suppress a preview.

## Safety and Compatibility

- Never execute Artifact content.
- Never extract archives.
- Never load remote or arbitrary local resources referenced by HTML or SVG.
- Never replace, move, publish, reject, or delete an Artifact from the Explorer.
- Preserve current Artifact UIDs, DB rows, paths, hashes, publication states, and migration compatibility.
- Preserve generic API behavior: unpublished Artifacts remain unavailable through `/v1/artifacts/{uid}` and `/v1/jobs/{id}/artifacts`.
- Add no third-party dependency; use Python standard library and PySide6 6.8+ modules already covered by the GUI extra.
- Keep image/PDF/archive failures local to the selected preview; the Artifact remains listed with metadata and external-open actions.
- Ignore stale async responses when the selected Task Run, Project Run, Review, or Artifact UID has changed.

## Testing Contract

Automated tests must cover:

- MIME/extension classification and UID deduplication.
- Primary/result/final selection policy.
- Path display, copy, open-file, and open-folder signals.
- JSON tree/raw, Markdown/raw, safe HTML, text/code, CSV/TSV, image, SVG, PDF, archive listing, truncation, missing file, and unsupported format states.
- Review candidate ownership checks and generic endpoint denial.
- Task Run tab consolidation and published/candidate merging.
- Project Run grouping, Pipeline navigation, candidate merging, and stale responses.
- Reviews Inbox candidate selection and refresh after actions.

Visual acceptance must use the real Qt platform at 1280x720 and 1024x700. The repository's offscreen Qt backend has no fonts and is unsuitable for visual judgment.

## Non-goals

- Editing Artifacts inside the Explorer.
- Office document conversion or embedded office-suite rendering.
- Audio/video playback.
- Syntax highlighting as a new dependency.
- Changing Artifact persistence, lineage, publication semantics, or search eligibility.
- Making the GUI work against a remote machine with inaccessible local paths.
