# Unified Artifact Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw or partial Artifact presentations in Task Runs, Project Runs, and Reviews with one safe, format-aware Artifact Explorer that shows paths and previews published and correctly scoped candidate files.

**Architecture:** Extract the existing Project Run Artifact preview into a reusable GUI-only component and keep each screen as a thin grouping/selection adapter. Preserve all generic published-Artifact API contracts; add only a Review-scoped candidate-content endpoint that verifies Review-round ownership before delegating to the existing bounded DB content reader. Treat the result file as the `role=result` Artifact and remove the duplicate Task Run `Result`/`Files` information split.

**Tech Stack:** Python 3.11+, PySide6 6.8+ (`QtWidgets`, `QtGui`, `QtSvg`, `QtPdf`, `QtPdfWidgets`), Python standard library (`csv`, `json`, `mimetypes`, `tarfile`, `zipfile`), authenticated Relay daemon API, SQLite Artifact records, `unittest`, Ruff.

**Design specification:** `docs/superpowers/specs/2026-08-12-unified-artifact-explorer-design.md`

## Global Constraints

- Work on a feature branch or isolated worktree; do not implement directly on `master`.
- Preserve Relay Home databases, manifests, Jobs, review candidates, outputs, Artifact UIDs, hashes, lineage, and migration compatibility.
- Do not add a DB migration or change Artifact publication semantics.
- Do not make candidate Artifacts visible through `/v1/artifacts/{uid}`, `/v1/jobs/{id}/artifacts`, search, or reusable input selection.
- Do not add third-party dependencies.
- Never execute Artifact content, extract an archive, or load resources referenced by HTML/SVG.
- Bound text to 262,144 bytes, CSV/TSV to 500 rows and 100 columns, archives to 2,000 entries, images to 40 million decoded pixels, and PDF in-app loading to 100 MiB.
- Keep `Answer` as a human-readable Task Run tab; represent raw/structured result evidence as the `role=result` Artifact.
- Keep Project Run tab order `Pipeline`, `Artifacts`, `Timeline`, `Orchestrator` and preserve Pipeline Artifact navigation.
- Keep candidate access review-scoped and validate current-round Task Run ownership on every read.
- Ignore stale asynchronous responses after Task Run, Project Run, Review, or Artifact selection changes.
- Automated GUI tests may use offscreen Qt, but visual acceptance must use the real Qt platform at 1280x720 and 1024x700.

## File Structure

- Create `relay/gui/artifacts.py`: normalized records, grouping/merge helpers, shared Explorer widget, safe preview renderers, and preview limits.
- Modify `relay/gui/project_runs.py`: retain a thin compatibility wrapper and Project-specific groups; remove embedded generic preview implementation.
- Modify `relay/gui/job_detail.py`: replace `Result`/`Files` with an `Artifacts` page and expose Artifact signals/cache methods.
- Modify `relay/gui/reviews.py`: compose Review metadata/actions with the shared Explorer.
- Modify `relay/gui/main_window.py`: generic Artifact preview routing, stale-response guards, OS path actions, and screen adapters.
- Modify `relay/search/__init__.py`: align bounded text-readable suffixes with the Preview matrix.
- Modify `relay/reviews/service.py`: authorize current-round candidate content.
- Modify `relay/api.py`: expose the Review-scoped content function.
- Modify `relay/daemon.py`: parse the Review Artifact content route before generic Review detail.
- Create `tests/test_artifacts_gui.py`: pure model and shared widget/renderer regressions.
- Create `tests/test_review_artifact_api.py`: Review ownership, publication boundary, size limit, and HTTP-route regressions.
- Modify `tests/test_g1_gui.py`, `tests/test_g2_gui.py`: Task Run request/response and information-architecture regressions.
- Modify `tests/test_project_runs_gui.py`: compatibility wrapper, review-candidate merge, navigation, and stale-response regressions.
- Create `tests/test_reviews_gui.py`: Review Explorer, action state, and refresh regressions.
- Modify `docs/Relay_GUI_Development_Plan_v1.3.md`, `wiki/project-model.md`, `memo.md`, and `log.md` only after implementation verification establishes the new current truth.

---

### Task 1: Extract the Artifact record model and shared Explorer shell

**Files:**
- Create: `relay/gui/artifacts.py`
- Create: `tests/test_artifacts_gui.py`
- Modify: `relay/gui/project_runs.py` imports and `ProjectRunArtifactsView`
- Test: `tests/test_project_runs_gui.py`

**Interfaces:**
- Consumes: Artifact dictionaries from existing Job, Project Run, and Review responses.
- Produces:
  - `ArtifactRecord.from_mapping(raw, *, node_id="", review_id="", is_primary=False) -> ArtifactRecord`
  - `ArtifactGroup(label: str, records: tuple[ArtifactRecord, ...])`
  - `artifact_kind(record: ArtifactRecord) -> str`
  - `merge_artifact_records(records: Iterable[ArtifactRecord]) -> list[ArtifactRecord]`
  - `ArtifactExplorerView.set_groups(groups, *, auto_select_primary: bool) -> None`
  - `ArtifactExplorerView.cache_artifact_detail(uid, raw)`, `cache_content(uid, payload)`, and `cache_error(uid, message)`
  - Signals `preview_requested(str uid, str review_id)`, `open_file_requested(str path)`, and `open_folder_requested(str path)`.

- [ ] **Step 1: Write failing model tests**

Add tests that pin normalization, MIME-first classification, UID deduplication, primary precedence, and review context:

```python
class ArtifactRecordTests(unittest.TestCase):
    def test_normalize_and_merge_preserve_primary_and_review_scope(self):
        summary = ArtifactRecord.from_mapping(
            {"artifact_uid": "a-1", "role": "result", "relative_path": "result.json"},
            is_primary=True,
        )
        detail = ArtifactRecord.from_mapping(
            {
                "artifact_uid": "a-1",
                "role": "result",
                "relative_path": "result.json",
                "final_path": "/relay/review/result.json",
                "mime_type": "application/json",
                "size": 42,
                "sha256": "abc",
                "publication_status": "candidate",
            },
            review_id="review-1",
        )
        merged = merge_artifact_records([summary, detail])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].is_primary)
        self.assertEqual(merged[0].review_id, "review-1")
        self.assertEqual(merged[0].final_path, "/relay/review/result.json")
        self.assertEqual(artifact_kind(merged[0]), "json")

    def test_mime_wins_over_misleading_extension(self):
        record = ArtifactRecord.from_mapping(
            {"artifact_uid": "a-2", "relative_path": "payload.bin", "mime_type": "text/csv"}
        )
        self.assertEqual(artifact_kind(record), "table")
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m unittest tests.test_artifacts_gui.ArtifactRecordTests -v`

Expected: import failure because `relay.gui.artifacts` does not exist.

- [ ] **Step 3: Implement the immutable record and merge rules**

Use these exact public fields; later tasks must not invent alternate names:

```python
@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_uid: str
    role: str = "output"
    name: str = ""
    relative_path: str = ""
    final_path: str = ""
    mime_type: str = ""
    size: int | None = None
    sha256: str = ""
    publication_status: str = "published"
    producer: str = "worker"
    source_task_run_id: str = ""
    node_id: str = ""
    review_id: str = ""
    is_primary: bool = False

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        node_id: str = "",
        review_id: str = "",
        is_primary: bool = False,
    ) -> "ArtifactRecord":
        relative_path = str(raw.get("relative_path") or raw.get("name") or "")
        return cls(
            artifact_uid=str(raw.get("artifact_uid") or ""),
            role=str(raw.get("role") or "output"),
            name=Path(relative_path).name if relative_path else "",
            relative_path=relative_path,
            final_path=str(raw.get("final_path") or ""),
            mime_type=str(raw.get("mime_type") or ""),
            size=int(raw["size"]) if raw.get("size") is not None else None,
            sha256=str(raw.get("sha256") or ""),
            publication_status=str(raw.get("publication_status") or "published"),
            producer=str(raw.get("producer") or "worker"),
            source_task_run_id=str(raw.get("job_id") or raw.get("task_run_id") or ""),
            node_id=str(raw.get("node_id") or node_id),
            review_id=str(raw.get("review_id") or review_id),
            is_primary=bool(raw.get("is_primary") or is_primary),
        )


@dataclass(frozen=True, slots=True)
class ArtifactGroup:
    label: str
    records: tuple[ArtifactRecord, ...]
```

Merge by non-empty UID. For legacy records without UID, use `(source_task_run_id, node_id, role, relative_path)` as the key. Fill missing metadata from later records, OR `is_primary`, and preserve a non-empty `review_id`.

- [ ] **Step 4: Write failing Explorer shell tests**

Test group rendering, automatic primary selection, no-selection behavior, selected metadata, path-copy behavior, and emitted signals:

```python
def test_explorer_selects_primary_and_emits_scoped_preview(self):
    view = ArtifactExplorerView()
    record = ArtifactRecord.from_mapping(
        {
            "artifact_uid": "candidate-1",
            "role": "result",
            "relative_path": "result.json",
            "final_path": "/relay/review/result.json",
            "publication_status": "candidate",
        },
        review_id="review-1",
        is_primary=True,
    )
    requests = []
    view.preview_requested.connect(lambda uid, review_id: requests.append((uid, review_id)))
    view.set_groups([ArtifactGroup("Current candidate", (record,))], auto_select_primary=True)
    self.assertEqual(view.selected_record().artifact_uid, "candidate-1")
    self.assertEqual(requests, [("candidate-1", "review-1")])
    self.assertIn("/relay/review/result.json", view.path_label.text())
```

- [ ] **Step 5: Implement the Explorer shell**

Build a left `QTreeWidget` and right preview area. The header must expose `role · filename`, a publication `StatusBadge`, selectable path text, MIME/size/SHA metadata, and `Open file`, `Open containing folder`, `Copy path` buttons. `Copy path` writes `selected_record().final_path` to `QApplication.clipboard()` inside the widget. File/folder buttons emit paths and do not call `QDesktopServices`.

The selection algorithm is exact:

```python
def _default_uid(groups: Sequence[ArtifactGroup], auto_select_primary: bool) -> str:
    records = [record for group in groups for record in group.records if record.artifact_uid]
    if not auto_select_primary:
        return ""
    primary = next((record for record in records if record.is_primary), None)
    return (primary or (records[0] if records else None)).artifact_uid if records else ""
```

Emit `preview_requested(uid, review_id)` only when the selected UID has neither cached content nor a cached error. Do not emit again during sparse metadata refreshes.

- [ ] **Step 6: Preserve Project Run compatibility while extracting**

In `relay/gui/project_runs.py`, import and re-export `_artifact_kind` as an alias for existing tests, and retain this thin wrapper:

```python
class ProjectRunArtifactsView(ArtifactExplorerView):
    def set_run(self, project_run_id, final_artifacts, task_artifacts):
        self.project_run_id = str(project_run_id or "")
        groups = project_artifact_groups(final_artifacts, task_artifacts)
        self.set_groups(groups, auto_select_primary=True)
```

Keep existing `cache_artifact_detail`, `cache_artifact_content`, `cache_artifact_error`, and `select_artifact` method names as delegating compatibility methods until all callers migrate.

Update the old Project Run empty-selection test in the same change: with a final Artifact present, the wrapper now selects that primary Artifact automatically; with no records, selection remains empty.

- [ ] **Step 7: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_artifacts_gui.ArtifactRecordTests tests.test_artifacts_gui.ArtifactExplorerShellTests -v
python -m unittest tests.test_project_runs_gui.ProjectRunArtifactsWidgetTests tests.test_project_runs_gui.ProjectRunDetailTabsTests -v
python -m ruff check relay/gui/artifacts.py relay/gui/project_runs.py tests/test_artifacts_gui.py tests/test_project_runs_gui.py
```

Expected: all selected tests pass and Ruff reports no errors.

Commit:

```bash
git add relay/gui/artifacts.py relay/gui/project_runs.py tests/test_artifacts_gui.py tests/test_project_runs_gui.py
git commit -m "refactor: extract shared artifact explorer"
```

---

### Task 2: Implement bounded, safe format renderers

**Files:**
- Modify: `relay/gui/artifacts.py`
- Modify: `relay/search/__init__.py`
- Modify: `tests/test_artifacts_gui.py`
- Modify: `tests/test_phase2.py`

**Interfaces:**
- Consumes: `ArtifactRecord`, bounded `{available, text, size, truncated, mime_type}` content payloads, and DB-backed local paths.
- Produces preview kinds: `json`, `json_lines`, `table`, `markdown`, `html`, `text`, `image`, `svg`, `pdf`, `archive`, `unsupported`.

- [ ] **Step 1: Add failing classification and text-content tests**

Cover every extension in the design matrix and assert MIME precedence. Extend `tests/test_phase2.py` with YAML/code content proving `/content` returns bounded UTF-8 text for the expanded suffix set:

```python
def test_artifact_content_reads_previewable_yaml_with_replacement(self):
    job, _ = self.engine.create_job(JobRequest(task="YAML preview", worker="codex"), queued=True)
    path = self.config.path_value("artifact_root") / job["job_id"] / "report.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"title: Relay\ninvalid: \xff")
    self.db.add_artifact(
        job["job_id"],
        relative_path="report.yaml",
        final_path=str(path),
        mime_type="application/yaml",
        size=path.stat().st_size,
        sha256="yaml",
        artifact_uid="yaml-artifact",
    )
    payload = artifact_content(self.db, "yaml-artifact", max_bytes=262144)
    self.assertTrue(payload["available"])
    self.assertIn("title: Relay", payload["text"])
```

- [ ] **Step 2: Run classification/content tests and confirm RED**

Run: `python -m unittest tests.test_artifacts_gui.ArtifactKindTests tests.test_phase2.Phase2Tests.test_artifact_content_reads_previewable_yaml_with_replacement -v`

Expected: unsupported classification or unavailable text for newly admitted suffixes.

- [ ] **Step 3: Align the bounded text suffix policy**

Set `_TEXT_SUFFIXES` in `relay/search/__init__.py` to the exact text/code extensions in the design spec plus `.jsonl` and `.ndjson`. Change `_read_text` to `data.decode("utf-8", errors="replace")`; suffix filtering and `max_bytes` remain mandatory, so binary files with unsupported suffixes remain unreadable.

- [ ] **Step 4: Add failing renderer tests**

Use temporary files and Qt-native fixture generation:

- JSON object and raw text.
- One malformed JSONL row.
- CSV with 501 rows and 101 columns, asserting the visible cap notice.
- Markdown rendered plus raw source.
- HTML containing `<script>`, `<iframe>`, `<object>`, and `<img src="file:///etc/passwd">`, asserting none remain in rendered HTML/resources.
- PNG generated by `QImage(20, 20, QImage.Format_ARGB32)`.
- SVG containing a simple local rectangle.
- PDF generated with `QPdfWriter` and `QPainter`.
- ZIP containing two names and a TAR containing two names.
- Missing file and unsupported `.docx` metadata fallback.

The PDF test must assert `view.preview_stack.currentWidget() is view.pdf_view`, not pixel appearance.

- [ ] **Step 5: Implement renderer helpers and limits**

Use constants with these exact values:

```python
MAX_TEXT_BYTES = 262_144
MAX_TABLE_ROWS = 500
MAX_TABLE_COLUMNS = 100
MAX_ARCHIVE_ENTRIES = 2_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_PDF_BYTES = 100 * 1024 * 1024
```

Implement a `SafeTextBrowser` whose `loadResource` returns `None`. Sanitize HTML with a standard-library `HTMLParser` allowlist containing only `p`, `div`, `span`, `h1`-`h6`, `ul`, `ol`, `li`, `table`, `thead`, `tbody`, `tr`, `th`, `td`, `pre`, `code`, `blockquote`, `b`, `strong`, `i`, `em`, `u`, `br`, and `hr`; preserve only integer `colspan`/`rowspan` attributes. Drop `script`, `style`, `iframe`, `object`, `embed`, `link`, `audio`, `video`, `img`, and all other tags/attributes. Keep raw HTML available in the Raw sub-tab.

For archives, call only `ZipFile.infolist()` or `TarFile.getmembers()` and never `extract`, `extractall`, or member `open`. Sort member rows by stored order and stop at `MAX_ARCHIVE_ENTRIES`.

For images, inspect dimensions with `QImageReader.size()` before decoding. Load SVG from verified bytes after rejecting XML elements whose local name is `script`, `foreignObject`, or `image`, plus any `href`/`xlink:href` attribute; then pass the bytes to `QSvgRenderer`. For PDF, check file size before `QPdfDocument.load(final_path)`. Every rejected or failed preview must show metadata and retain external-open buttons.

- [ ] **Step 6: Run renderer and content tests**

Run:

```bash
python -m unittest tests.test_artifacts_gui -v
python -m unittest tests.test_phase2 -v
python -m ruff check relay/gui/artifacts.py relay/search/__init__.py tests/test_artifacts_gui.py tests/test_phase2.py
```

Expected: all tests pass; no archive extraction is present (`rg -n "extractall|\.extract\(" relay/gui/artifacts.py` returns no matches).

- [ ] **Step 7: Commit**

```bash
git add relay/gui/artifacts.py relay/search/__init__.py tests/test_artifacts_gui.py tests/test_phase2.py
git commit -m "feat: render common artifact formats safely"
```

---

### Task 3: Add Review-scoped candidate content access

**Files:**
- Modify: `relay/reviews/service.py`
- Modify: `relay/api.py`
- Modify: `relay/daemon.py`
- Create: `tests/test_review_artifact_api.py`
- Modify: `tests/test_reviews.py`

**Interfaces:**
- Consumes: Review session, current Review round, Artifact DB row, and existing `Database.artifact_content`.
- Produces:
  - `ReviewService.artifact_content(review_id: str, artifact_uid: str, max_bytes: int) -> dict[str, Any]`
  - `review_artifact_content(engine, review_id, artifact_uid, max_bytes=65536) -> dict[str, Any]`
  - `GET /v1/reviews/{review_id}/artifacts/{artifact_uid}/content?max_bytes=N`.

- [ ] **Step 1: Write failing service authorization tests**

Create two review candidate Jobs. Assert that the correct Review reads its own candidate, cross-Review access raises `ARTIFACT_NOT_FOUND`, unknown Review raises `REVIEW_NOT_FOUND`, and the generic `artifact_content` API still rejects the candidate:

```python
def test_review_content_is_current_round_scoped(self):
    own = self.service.artifact_content("review-a", "artifact-a", 4)
    self.assertEqual(own["text"], "cand")
    with self.assertRaisesRegex(RelayError, "Artifact not found"):
        self.service.artifact_content("review-a", "artifact-b", 262144)
    with self.assertRaisesRegex(RelayError, "Artifact not found"):
        artifact_content(self.db, "artifact-a", max_bytes=262144)
```

- [ ] **Step 2: Run service tests and confirm RED**

Run: `python -m unittest tests.test_review_artifact_api.ReviewArtifactContentServiceTests -v`

Expected: `ReviewService` has no `artifact_content` method.

- [ ] **Step 3: Implement exact ownership validation**

Add this method without changing `Database.artifact_content`:

```python
def artifact_content(self, review_id: str, artifact_uid: str, max_bytes: int) -> dict[str, Any]:
    session = self.db.get_review_session(review_id)
    if not session:
        raise RelayError("REVIEW_NOT_FOUND", f"Review not found: {review_id}")
    rounds = self.db.list_review_rounds(review_id)
    current = rounds[-1] if rounds else None
    artifact = self.db.artifact_by_uid(artifact_uid)
    if (
        not current
        or not artifact
        or artifact.get("job_id") != current.get("task_run_id")
        or artifact.get("publication_status") not in {"candidate", "published", "rejected"}
    ):
        raise RelayError("ARTIFACT_NOT_FOUND", f"Artifact not found: {artifact_uid}")
    return self.db.artifact_content(artifact_uid, max_bytes)
```

- [ ] **Step 4: Write failing HTTP route tests**

Start `RelayDaemon` on a free port as in `tests/test_phase4_api.py`. Test status 200 for the owning route, 404 for cross-review UID, 404 for generic candidate content, and `max_bytes=4` truncation. Include percent-encoded IDs in the request path.

- [ ] **Step 5: Implement API and route parsing before generic Review detail**

In `relay/api.py`, add a thin function that normalizes `max_bytes` and calls the service. In `RelayRequestHandler.do_GET`, parse this shape before `GET /v1/reviews/{review_id}`:

```python
parts = path.strip("/").split("/")
if len(parts) == 6 and parts[:2] == ["v1", "reviews"] and parts[3] == "artifacts" and parts[5] == "content":
    review_id = unquote(parts[2])
    artifact_uid = unquote(parts[4])
    limit = int((params.get("max_bytes") or ["65536"])[0])
    self._json(
        HTTPStatus.OK,
        review_artifact_content(self.daemon.engine, review_id, artifact_uid, max_bytes=limit),
    )
    return
```

Map `REVIEW_NOT_FOUND` and `ARTIFACT_NOT_FOUND` to 404; invalid `max_bytes` maps to 400 `INVALID_REQUEST`.

- [ ] **Step 6: Run API/security tests and commit**

Run:

```bash
python -m unittest tests.test_review_artifact_api tests.test_reviews -v
python -m ruff check relay/reviews/service.py relay/api.py relay/daemon.py tests/test_review_artifact_api.py tests/test_reviews.py
```

Expected: owning candidate content succeeds, all cross-scope/generic reads fail, and existing Review lifecycle tests pass.

Commit:

```bash
git add relay/reviews/service.py relay/api.py relay/daemon.py tests/test_review_artifact_api.py tests/test_reviews.py
git commit -m "feat: scope candidate artifact preview to reviews"
```

---

### Task 4: Centralize GUI Artifact request routing and path actions

**Files:**
- Modify: `relay/gui/main_window.py`
- Modify: `tests/test_artifacts_gui.py`
- Modify: `tests/test_project_runs_gui.py`

**Interfaces:**
- Consumes: Explorer signals `(uid, review_id)`, screen owner kind/ID, generic Artifact detail/content routes, and Review-scoped content route.
- Produces:
  - `_request_artifact_preview(owner_kind, owner_id, artifact_uid, review_id="")`
  - `_artifact_explorer_for(owner_kind, owner_id) -> ArtifactExplorerView | None`
  - Pending kinds `artifact_detail` and `artifact_content` containing owner kind, owner ID, UID, and Review ID.

- [ ] **Step 1: Write failing routing tests**

Cover these exact paths:

```python
window._request_artifact_preview("task_run", "job-1", "a-1", "")
self.assertEqual(requests[-1][1], "/v1/artifacts/a-1")

window._request_artifact_preview("review", "review-1", "a-2", "review-1")
self.assertEqual(
    requests[-1][1],
    "/v1/reviews/review-1/artifacts/a-2/content?max_bytes=262144",
)
```

Also assert a response is ignored when the selected owner ID changed and that `Open file`/`Open containing folder` call `_open_path` with `file_only=True`/`directory_only=True`.

- [ ] **Step 2: Run routing tests and confirm RED**

Run: `python -m unittest tests.test_artifacts_gui.ArtifactMainWindowRoutingTests -v`

Expected: `_request_artifact_preview` and generic pending kinds do not exist.

- [ ] **Step 3: Implement generic routing**

Use owner kinds exactly `task_run`, `project_run`, and `review`. Generic published selection first requests metadata; metadata success calls `cache_artifact_detail`, then requests `/content` only for `json`, `json_lines`, `table`, `markdown`, `html`, or `text`. Review selection skips generic metadata because `GET /v1/reviews/{id}` already supplied the DB record and requests only the Review-scoped content route for text-like kinds.

The response handler must call `_artifact_explorer_for`; returning `None` is a stale response and must have no UI effect. Error responses call `cache_error(uid, bounded_message)` only on the still-current Explorer.

- [ ] **Step 4: Delete project-specific duplicated routing only after compatibility tests pass**

Replace `_preview_project_run_artifact` and `project_run_artifact_detail/content` pending branches with the generic helpers. Keep `_open_project_run_artifact` only until Task 6 confirms no caller remains; then remove it.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_artifacts_gui.ArtifactMainWindowRoutingTests tests.test_project_runs_gui.ProjectRunInspectorMainWindowRoutingTests -v
python -m ruff check relay/gui/main_window.py tests/test_artifacts_gui.py tests/test_project_runs_gui.py
```

Commit:

```bash
git add relay/gui/main_window.py tests/test_artifacts_gui.py tests/test_project_runs_gui.py
git commit -m "refactor: centralize artifact preview routing"
```

---

### Task 5: Replace Task Run Result/Files duplication with Artifacts

**Files:**
- Modify: `relay/gui/job_detail.py`
- Modify: `relay/gui/main_window.py`
- Modify: `tests/test_g1_gui.py`
- Modify: `tests/test_g2_gui.py`

**Interfaces:**
- Consumes: published `job["artifacts"]`, Review detail `job["review"]["artifacts"]`, and generic routing from Task 4.
- Produces:
  - Task Run tabs `Overview`, `Task`, `Inputs`, `Progress`, `Answer`, `Artifacts`, `Logs`, `Events`.
  - `TaskRunDetailView.artifact_preview_requested(uid, review_id)`.
  - `TaskRunDetailView.set_published_artifacts(items)` and `cache_artifact_*` forwarding methods.

- [ ] **Step 1: Update tests first and confirm the old UI fails**

Change the tab assertion to:

```python
self.assertEqual(
    labels,
    ["Overview", "Task", "Inputs", "Progress", "Answer", "Artifacts", "Logs", "Events"],
)
```

Add tests proving the `role=result` record is first and selected, a pending Review candidate is visible with `review_id`, and sparse detail refresh preserves selected UID/content.

Run: `python -m unittest tests.test_g2_gui.G2TaskRunGuiTests -v`

Expected: tab assertion and Artifact widget tests fail against the old `Result`/`Files` layout.

- [ ] **Step 2: Replace the two browser pages with one Explorer page**

Instantiate `ArtifactExplorerView` for the `Artifacts` tab. Build groups as follows:

```python
published = [ArtifactRecord.from_mapping(item, is_primary=item.get("role") == "result") for item in artifacts]
candidate = [
    ArtifactRecord.from_mapping(
        item,
        review_id=review_id,
        is_primary=item.get("role") == "result",
    )
    for item in review_artifacts
]
records = merge_artifact_records([*published, *candidate])
result_records = tuple(item for item in records if item.role == "result")
other_records = tuple(item for item in records if item.role != "result")
groups = [ArtifactGroup("Result", result_records), ArtifactGroup("Artifacts", other_records)]
```

Drop empty groups. Forward Explorer preview/file/folder signals through the Task Run detail view.

- [ ] **Step 3: Change Task Run request/response behavior**

`_detail_tab_requested("Artifacts")` requests `/v1/jobs/{job_id}/artifacts`. The `artifacts` response calls `set_published_artifacts`; it must never call `_format_payload`. The `result` response remains only for `Answer` extraction and no longer writes a `Result` browser.

When `job_detail` includes `review`, merge `review["artifacts"]` immediately so candidates appear before generic published Artifact loading completes.

Remove the dead `set_content("Review", ...)` block in `TaskRunDetailView.set_job`; there is no Review tab. Add `("Review", job.get("review_status"))` to the Overview metadata table so Review state remains visible while actions stay in the Reviews Inbox.

- [ ] **Step 4: Run Task Run regressions and commit**

Run:

```bash
python -m unittest tests.test_g1_gui tests.test_g2_gui tests.test_runs_gui -v
python -m ruff check relay/gui/job_detail.py relay/gui/main_window.py tests/test_g1_gui.py tests/test_g2_gui.py
```

Expected: Task Run tab and routing tests pass; no `set_content("Files"` or `set_content("Result"` calls remain (`rg -n 'set_content\("(Files|Result)"' relay/gui` returns no matches).

Commit:

```bash
git add relay/gui/job_detail.py relay/gui/main_window.py tests/test_g1_gui.py tests/test_g2_gui.py
git commit -m "feat: unify task run evidence under artifacts"
```

---

### Task 6: Merge published and review-candidate Artifacts in Project Runs

**Files:**
- Modify: `relay/gui/project_runs.py`
- Modify: `relay/gui/main_window.py`
- Modify: `tests/test_project_runs_gui.py`

**Interfaces:**
- Consumes: final Artifact summaries, per-node published Artifact responses, Project Run Review summaries, and Review detail responses.
- Produces:
  - `ProjectRunDetailView.cache_review_artifacts(node_id, review_id, artifacts)`.
  - Pending kind `project_run_review_detail(project_run_id, review_id, node_id)`.
  - Project groups in project-definition node order with candidate state preserved.

- [ ] **Step 1: Write failing candidate-merge tests**

Assert an awaiting-review node displays its result and non-result candidates, generic node Artifact response can be empty without removing candidates, and a published response with the same UID replaces missing candidate metadata without losing selection.

Add routing coverage:

```python
window.pending[310] = ("project_run_v2_reviews", "pr-1")
window._handle_response(
    310,
    {"reviews": [{"review_id": "r-1", "project_run_id": "pr-1", "node_id": "draft", "status": "pending_human"}]},
    None,
)
self.assertIn(("project_run_review_detail", "pr-1", "r-1", "draft"), [kind for kind, _ in requests])
```

- [ ] **Step 2: Run focused Project tests and confirm RED**

Run: `python -m unittest tests.test_project_runs_gui.ProjectRunArtifactsWidgetTests tests.test_project_runs_gui.ProjectRunsMainWindowRoutingTests -v`

- [ ] **Step 3: Add a separate candidate cache and deterministic group merge**

Keep `_node_artifacts` for published data and add `_review_artifacts: dict[str, list[dict]]`. Reset both when selected Project Run ID changes. `cache_review_artifacts` annotates every record with `node_id`, `review_id`, `publication_status`, and `is_primary = role == "result"` before re-rendering Pipeline and Artifacts.

Do not put candidate chips in Pipeline unless the node is `awaiting_review`; published chips retain current behavior.

- [ ] **Step 4: Fetch actionable Review details lazily**

On `project_run_v2_reviews`, request detail only for statuses `pending_human`, `needs_human`, `delivery_failed`, or `evaluating`. On detail success, extract `payload["artifacts"]` and call `cache_review_artifacts`. Include Project Run ID in the pending tuple and ignore stale responses.

- [ ] **Step 5: Preserve Pipeline navigation and selection**

Double-clicking/click-activating an Artifact chip still switches to the existing `Artifacts` tab and selects the UID. If final metadata arrives after selection, the same UID remains selected and the Preview refreshes in place.

- [ ] **Step 6: Run Project Run regressions and commit**

Run:

```bash
python -m unittest tests.test_project_runs_gui -v
python -m ruff check relay/gui/project_runs.py relay/gui/main_window.py tests/test_project_runs_gui.py
```

Expected: all Project Run tests pass, including existing Pipeline/Inspector/Timeline/Orchestrator tests.

Commit:

```bash
git add relay/gui/project_runs.py relay/gui/main_window.py tests/test_project_runs_gui.py
git commit -m "feat: show review candidates in project artifacts"
```

---

### Task 7: Embed the shared Explorer in Reviews Inbox

**Files:**
- Modify: `relay/gui/reviews.py`
- Modify: `relay/gui/main_window.py`
- Create: `tests/test_reviews_gui.py`

**Interfaces:**
- Consumes: `GET /v1/reviews/{id}` payload with session, current round, Task Run, Artifacts, and candidate result metadata.
- Produces:
  - `ReviewsView.artifact_preview_requested(uid, review_id)`.
  - `ReviewsView.open_file_requested(path)` and `open_folder_requested(path)`.
  - `ReviewsView.cache_artifact_*` forwarding methods.

- [ ] **Step 1: Write failing Review widget tests**

Test that a selected Review shows status/round/guidelines, groups all current-round candidate Artifacts, auto-selects the result Artifact, shows candidate status/path, and leaves confirm/rerun/reject state unchanged. Test no-review and unavailable-preview states.

```python
def test_review_selects_result_candidate_and_keeps_actions(self):
    view = ReviewsView()
    view.set_review(
        {
            "review": {"review_id": "r-1", "status": "pending_human", "scope_type": "task"},
            "current_round": {"round_no": 2, "task_run_id": "job-2"},
            "artifacts": [
                {"artifact_uid": "notes", "role": "output", "relative_path": "notes.md", "publication_status": "candidate"},
                {"artifact_uid": "result", "role": "result", "relative_path": "result.json", "publication_status": "candidate"},
            ],
        }
    )
    self.assertEqual(view.artifacts.selected_record().artifact_uid, "result")
    self.assertTrue(view.confirm.isEnabled())
    self.assertTrue(view.rerun.isEnabled())
    self.assertTrue(view.reject.isEnabled())
```

- [ ] **Step 2: Run Review GUI tests and confirm RED**

Run: `python -m unittest tests.test_reviews_gui -v`

- [ ] **Step 3: Replace text-only file/result preview with the Explorer**

Keep the left Review list. On the right, render a compact metadata header, then `ArtifactExplorerView` with stretch factor 1, then feedback and action controls. Remove the `<ul>Result files</ul>` and `<pre>Current result preview</pre>` HTML construction. Annotate every record with the selected `review_id` before grouping it under `Current candidate`.

- [ ] **Step 4: Wire candidate content and refresh after actions**

Connect Review preview requests to generic routing with owner kind `review`. On `review_action` success:

1. Call `reviews_view.set_review(payload)` when the payload still contains the selected Review.
2. Refresh the Reviews list.
3. If the affected Task Run is selected, request its detail.
4. If the affected Project Run is selected, request its detail, steps, and reviews.

This refresh updates candidate/published status and final path without navigating away from the user's current screen.

- [ ] **Step 5: Run Review and cross-screen regressions**

Run:

```bash
python -m unittest tests.test_reviews_gui tests.test_reviews tests.test_g1_gui tests.test_project_runs_gui -v
python -m ruff check relay/gui/reviews.py relay/gui/main_window.py tests/test_reviews_gui.py
```

- [ ] **Step 6: Commit**

```bash
git add relay/gui/reviews.py relay/gui/main_window.py tests/test_reviews_gui.py
git commit -m "feat: preview candidate artifacts during review"
```

---

### Task 8: Documentation, full verification, and real-Qt acceptance

**Files:**
- Modify: `docs/Relay_GUI_Development_Plan_v1.3.md`
- Modify: `wiki/project-model.md`
- Modify: `memo.md`
- Modify: `log.md`
- Verify: all implementation and test files from Tasks 1-7

**Interfaces:**
- Consumes: verified implementation and actual test output.
- Produces: current documentation, resolved/open memory state, and release-quality verification evidence.

- [ ] **Step 1: Run focused tests together**

```bash
python -m unittest \
  tests.test_artifacts_gui \
  tests.test_review_artifact_api \
  tests.test_reviews_gui \
  tests.test_reviews \
  tests.test_g1_gui \
  tests.test_g2_gui \
  tests.test_project_runs_gui \
  tests.test_phase2 -v
```

Expected: zero failures and zero errors. Environment-dependent GUI module absence may skip GUI files only in non-GUI environments; the actual acceptance machine must have PySide6.

- [ ] **Step 2: Run repository quality gates**

```bash
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests
python build_release.py
python relay.pyz version
git diff --check
```

Expected: every command exits 0. If the known repository-wide format debt still exists, record the exact pre-existing files and separately require changed-file format/check plus all tests/build/version/diff checks to exit 0; do not claim the full format gate passed.

- [ ] **Step 3: Run the real-Qt manual matrix**

At both 1280x720 and 1024x700, inspect:

- Published Task Run with JSON result, Markdown, CSV, image, PDF, ZIP, and unsupported DOCX.
- Pending Task Review with result plus at least two candidate Artifacts.
- Project Run with final Artifacts and artifacts from at least three nodes.
- Project node awaiting Review with candidate Artifact chips and Explorer entries.
- Missing local file, malformed JSON/JSONL, oversized table/archive/image/PDF, and content request failure.
- Confirm then refresh: status changes to Published and path changes from review candidate root to final output/artifact root.
- Rerun then refresh: latest Review round replaces current candidate selection.
- `Open file`, `Open containing folder`, and `Copy path` on Windows, Ubuntu, and macOS where CI/manual hosts are available.

The app must never render tofu glyphs as acceptance evidence; do not use `QT_QPA_PLATFORM=offscreen` for this step.

- [ ] **Step 4: Update current documentation and memory**

Document the Task Run tab contract, shared Explorer matrix, Review-scoped candidate boundary, and external-open fallback in `docs/Relay_GUI_Development_Plan_v1.3.md` and `wiki/project-model.md`. Remove any `memo.md` item made obsolete by the verified feature, retain unresolved real-Qt/platform checks, and add one newest-first `log.md` line under 200 characters using the actual verification result and local timestamp.

- [ ] **Step 5: Final diff audit**

Run:

```bash
git status --short
git diff --stat
git diff --check
rg -n "T[B]D|T[O]DO|implement[ ]later|add[ ]appropriate|similar[ ]to[ ]Task" docs/superpowers/plans/2026-08-12-unified-artifact-explorer.md
```

Expected: only intentional implementation/docs/tests are changed; the plan scan returns no matches; user-owned `relay-receipt.json`, `test_result.json`, and `test_task.md` remain untracked and untouched if still present.

- [ ] **Step 6: Commit verified docs and memory**

```bash
git add docs/Relay_GUI_Development_Plan_v1.3.md wiki/project-model.md memo.md log.md
git commit -m "docs: record unified artifact explorer contract"
```

## Completion Criteria

- Task Run shows `Artifacts` instead of duplicate `Result` and `Files` tabs, while `Answer` remains available.
- Task Run result Artifact and all produced Artifacts are visible with filename, relative path, full path, MIME, size, SHA-256, and publication status.
- Project Run shows final, node-produced, and active Review candidate Artifacts through the same Explorer.
- Reviews Inbox previews every current-round candidate Artifact before confirmation.
- Candidate content is readable only through an owning Review route and remains absent from generic Artifact/search/reuse APIs.
- JSON, JSONL, CSV/TSV, Markdown, safe HTML, text/code, raster image, SVG, PDF, and archive-list previews obey all limits and fallbacks.
- File open, containing-folder open, and path copy behave consistently in all three screens.
- Stale async responses cannot replace the currently selected Artifact or screen.
- Focused tests, full tests, build, version check, and `git diff --check` pass; any known full-format debt is reported precisely rather than hidden.
