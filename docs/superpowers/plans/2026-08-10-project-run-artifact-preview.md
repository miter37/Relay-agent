# Project Run Artifact Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the overlapping Steps tab with an Artifacts tab that lists every Project Run Artifact by Task, pins final Artifacts at the top, and renders a selected Artifact in a large format-aware preview.

**Architecture:** Keep Pipeline and Timeline focused on execution structure and elapsed time. Add a dedicated `ProjectRunArtifactsView` read model inside the existing Project Run detail view; it merges final Artifact summaries with per-Task Artifact responses, deduplicates by `artifact_uid`, and owns list selection plus preview rendering. Pipeline node cards expose compact Artifact chips; double-clicking a chip switches to Artifacts and selects that Artifact. Existing `/v1/artifacts/{uid}` and `/v1/artifacts/{uid}/content` API contracts are reused.

**Tech Stack:** PySide6 widgets, existing Relay daemon Artifact detail/content APIs, `unittest`, Ruff, real Qt smoke verification.

## Global Constraints

- Do not change or delete Relay Home Artifacts, paths, manifests, or history.
- Do not add a third-party preview dependency.
- Artifact preview must be read-only and must never execute Artifact content.
- The Artifacts tab opens with no selected Artifact; selection comes from a list click or Pipeline double-click.
- JSON is shown as an expandable structure tree by default, not as raw JSON text.
- HTML, Markdown, and images render in-app when the existing Artifact metadata/content makes that safe and available; unsupported formats show metadata and an external-open action.
- Preserve existing `ProjectRunPipelineView.set_run(...)`, `node_selected`, and `open_output_requested` compatibility behavior; only add optional parameters/signals.
- Keep existing sparse-detail merge and Inspector state behavior intact.

---

### Task 1: Define Artifact view-model helpers and regression fixtures

**Files:**
- Modify: `relay/gui/project_runs.py` near the existing Project Run formatting helpers
- Modify: `tests/test_project_runs_gui.py`
- Modify: `tests/fixtures/project_run_cases.py` if fixture Artifact fields are missing

**Interfaces:**
- Add pure helpers `_artifact_uid(artifact) -> str`, `_artifact_kind(artifact) -> str`, and `_merge_project_run_artifacts(final_artifacts, task_artifacts) -> list[dict[str, Any]]`.
- `_merge_project_run_artifacts` returns stable entries with `artifact_uid`, `node_id`, `role`, `relative_path`, `mime_type`, `final_path`, and boolean `is_final`; duplicate UIDs are merged with final-output information winning.

- [ ] **Step 1: Write failing tests** for final Artifact pinning, Task grouping metadata, UID deduplication, extension/MIME classification for JSON/Markdown/HTML/image/PDF/unknown, and an Artifact without a UID.
- [ ] **Step 2: Run the focused tests and confirm they fail** because the helpers do not exist.
- [ ] **Step 3: Implement the pure helpers** without touching widget code; use MIME first, then the lower-case `relative_path` suffix.
- [ ] **Step 4: Run the focused tests and confirm they pass.**

### Task 2: Build the Artifacts tab with structured previews

**Files:**
- Modify: `relay/gui/project_runs.py` by adding `ProjectRunArtifactsView` and its preview widgets
- Modify: `tests/test_project_runs_gui.py`

**Interfaces:**
- `ProjectRunArtifactsView` exposes `artifact_preview_requested = Signal(str)` and `open_artifact_requested = Signal(str)`.
- `set_run(project_run_id, final_artifacts, task_artifacts)` rebuilds the grouped list without selecting an item.
- `select_artifact(artifact_uid)` selects an existing item and renders it; it emits `artifact_preview_requested` only when content/detail is missing.
- `cache_artifact_detail(artifact_uid, artifact)` and `cache_artifact_content(artifact_uid, content)` update the selected preview without changing selection.
- `cache_artifact_error(artifact_uid, message)` shows a bounded unavailable state.

- [ ] **Step 1: Write failing widget tests** for the empty initial selection, `Final Artifacts` group before Task groups, one group per node/Task, click selection, and missing-Artifact state.
- [ ] **Step 2: Write failing preview tests** for JSON tree nodes, rendered HTML/Markdown text, image metadata/path handling, unsupported format messaging, and content-error messaging.
- [ ] **Step 3: Run the focused tests and confirm they fail** because the Artifacts widget is not present.
- [ ] **Step 4: Implement the minimal Artifacts widget:** a grouped `QTreeWidget` on the left and a large preview stack on the right containing a structured `QTreeWidget`, `QTextBrowser`, image `QLabel`, and metadata/unavailable pages. Do not auto-select the first item.
- [ ] **Step 5: Implement JSON tree recursion** using object keys and array indexes as tree labels; keep raw JSON out of the default page.
- [ ] **Step 6: Implement safe format rendering:** use `QTextBrowser.setHtml` for HTML, `document().setMarkdown` for Markdown, `QPixmap` only for an existing local image file, and metadata-only fallback for unsupported or unavailable content.
- [ ] **Step 7: Run the focused widget tests and confirm they pass.**

### Task 3: Replace Steps with Artifacts in Project Run detail

**Files:**
- Modify: `relay/gui/project_runs.py:ProjectRunDetailView`
- Modify: `tests/test_project_runs_gui.py`

**Interfaces:**
- `ProjectRunDetailView` creates `artifacts_view` and adds tabs in this order: `Pipeline`, `Artifacts`, `Timeline`.
- `ProjectRunDetailView.artifact_preview_requested` forwards the Artifacts view request to `MainWindow`.
- `_render_artifacts_tab()` passes `final_artifact_ids` and the cached per-node Artifact lists to the view.
- `cache_node_artifacts` re-renders the Artifacts tab and Pipeline chips while preserving any selected Artifact.

- [ ] **Step 1: Add failing tests** asserting Steps is absent, Artifacts is present, final artifacts are pinned, and per-node artifacts survive a later lazy API response.
- [ ] **Step 2: Run the focused detail tests and confirm the old tab contract fails.**
- [ ] **Step 3: Replace only the tab wiring** and preserve Inspector visibility rules for Pipeline/Timeline; Artifacts never displays the node Inspector automatically.
- [ ] **Step 4: Connect Artifact view signals** to the existing output-opening signal and a new preview-request signal.
- [ ] **Step 5: Run Project Run detail tests and confirm they pass.**

### Task 4: Add Pipeline Artifact chips and double-click navigation

**Files:**
- Modify: `relay/gui/project_runs.py:ProjectRunPipelineView, ProjectRunNodeCard, ProjectRunDetailView`
- Modify: `tests/test_project_runs_gui.py`

**Interfaces:**
- Add `ProjectRunPipelineView.artifact_selected = Signal(str)` and optional `node_artifacts` to `set_run(...)`.
- Add a compact `ProjectRunArtifactChip` child widget with `double_clicked = Signal(str)`; its double-click emits the Artifact UID without opening an external application.
- `ProjectRunDetailView._on_pipeline_artifact_selected(uid)` switches to `artifacts_view` and calls `select_artifact(uid)`.

- [ ] **Step 1: Write failing tests** for chips on the correct node, absent chips when no Artifact exists, and double-click emission of the Artifact UID.
- [ ] **Step 2: Run the focused Pipeline tests and confirm they fail.**
- [ ] **Step 3: Implement compact chips** using role/path basename only; keep Worker, duration, and Task Run metadata out of the card.
- [ ] **Step 4: Pass cached node Artifacts into Pipeline and wire double-click navigation** while preserving ordinary card click-to-Inspector behavior.
- [ ] **Step 5: Run Pipeline/detail tests and confirm they pass.**

### Task 5: Add API response routing for preview content

**Files:**
- Modify: `relay/gui/main_window.py:_select_project_run, _handle_response, artifact request helpers`
- Modify: `tests/test_project_runs_gui.py`

**Interfaces:**
- Add `_preview_project_run_artifact(artifact_uid)` which requests `/v1/artifacts/{uid}` for the selected Project Run.
- On detail success, cache the Artifact metadata and request `/v1/artifacts/{uid}/content?max_bytes=262144` for text-like formats.
- Add pending kinds `project_run_artifact_detail`, `project_run_artifact_content`; route success and error to the selected `ProjectRunArtifactsView` only when the Project Run ID still matches.
- Preserve `_open_project_run_artifact` as the existing external-open action.

- [ ] **Step 1: Add failing routing tests** for detail responses, content responses, direct/malformed payloads, stale Project Run responses, and bounded error display.
- [ ] **Step 2: Run the focused routing tests and confirm they fail.**
- [ ] **Step 3: Implement request/response routing** with response adapters accepting `{artifact: {...}}` and direct Artifact objects for compatibility.
- [ ] **Step 4: Run routing tests and confirm they pass.**

### Task 6: Update documentation and verify the full feature

**Files:**
- Modify: `docs/Relay_GUI_Project_Runs_Screen_Design_v1.0.md`
- Modify: `memo.md`
- Modify: `log.md`
- Test: `tests/test_project_runs_gui.py`

- [ ] **Step 1: Add an Artifacts acceptance matrix** covering success with final outputs, failed/partial output, missing Artifact, JSON, HTML, image, and unsupported formats.
- [ ] **Step 2: Run the full suite:** `python -m unittest discover -s tests`; expected: zero failures.
- [ ] **Step 3: Run static checks:** `python -m ruff check relay/gui/project_runs.py relay/gui/main_window.py tests/test_project_runs_gui.py tests/fixtures/project_run_cases.py` and `git diff --check`.
- [ ] **Step 4: Start the GUI on real Qt** and verify initial no-selection, grouped lists, Pipeline double-click navigation, JSON tree, HTML/image preview, missing-file state, and external-open fallback.
- [ ] **Step 5: Update memory** with the new tab contract and record the verified test count in `log.md`.
