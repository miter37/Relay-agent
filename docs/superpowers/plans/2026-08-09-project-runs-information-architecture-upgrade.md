# Project Runs Information Architecture Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Project Runs a decision-oriented execution console where Pipeline explains structure and causality, Steps explains the execution ledger, and the Inspector explains one selected node without duplicating or losing information.

**Architecture:** Keep one selected-Run detail state in `ProjectRunsView`, but separate three read models: graph topology, execution ledger, and selected-node evidence. Upgrade the Pipeline to a scrollable graph with correctly positioned edges, make Steps the canonical chronological/detail table, and make the Inspector an explicit toggleable drawer whose state survives refreshes. Reuse existing Project Run snapshots, step rows, receipts, Task Run details, attempts, and final Artifact data; add backend fields only where the existing API cannot provide stable UI data.

**Tech Stack:** PySide6, Qt `QGraphicsView/QGraphicsScene` for the DAG, existing Relay Project Run API/SQLite catalog, `unittest`, Ruff, real-Qt GUI verification on `DISPLAY=:1`.

## Global Constraints

- Preserve Project Run history, snapshots, Task Run lineage, Artifacts, and existing GUI/API compatibility fields.
- Do not delete or rewrite the successful or failed Project Run records used as regression evidence.
- Pipeline and Steps must have visibly different responsibilities; do not repeat the full Steps table inside Pipeline cards.
- Terminal Runs must not be polled continuously; selected live Runs may refresh at the existing interval.
- `actual_worker` is authoritative when available; distinguish requested Worker, override, and actual fallback Worker.
- Failed, blocked, cancelled, partial, and awaiting-approval states must remain distinct in words, icons, and data—not by color alone.
- Every behavior change gets a failing regression test before implementation; run Ruff and the full unittest suite before completion.
- GUI visual checks use real Qt, never `QT_QPA_PLATFORM=offscreen`.

---

## Evidence and Product Decisions

The plan is grounded in the current Relay Home records and the existing Project Runs design document.

| Evidence | What the screen must make obvious |
|---|---|
| Successful `[L1]` Run `01KZJNESM7J7YXJ5SAXCQMW37M`: 6 steps, two parallel research roots, 3 final Artifacts, completed in about 9 minutes | Parallelism, dependency flow, final `report_json`/`final_report`/`assets_bundle`, and the completed verdict |
| Failed Run `01KZJMJZBWC1S5NPSS7BGMEBHY`: four completed, `image_collection` failed with `schema_version` mismatch, one descendant blocked | The first failure, exact humanized cause, blocked descendant, and preserved partial evidence |
| Failed Runs `01KZJMDZ4SP8TX0KWPX853JQX4` and related runs: `Unsupported worker: agy`, two failed/4 blocked | Registration/dispatch failure must be shown before downstream work, and blocked nodes must not look like failed nodes |
| Cancelled Run `01KZJN9RWNJBE85CRBRGMK6DVR` | Cancellation is an intentional terminal state, not a Worker or Task failure |
| Current screen | Pipeline cards repeat node/status/worker/duration shown in Steps; Steps is alphabetically ordered unless a snapshot is present; Inspector is a separate panel and needs explicit state management |

### Final information architecture

| Surface | Primary question | It owns | It must not duplicate |
|---|---|---|---|
| Pipeline | “How does this Project flow, and where did causality break?” | DAG nodes, edges, parallel branches, failed→blocked propagation, compact status | Full Worker/duration/error/Task Run columns |
| Steps | “What actually ran, in what order, with what evidence?” | Sequence, node, status, attempt count, started/duration, requested→actual Worker, error, Task Run | Graph edges and large node cards |
| Timeline | “Where did time go, including retries and parallel gaps?” | Attempt bars, retry gaps, parallel spans, blocked/not-started markers | Full node evidence panel |
| Inspector | “Why did this one node end in this state?” | Attempts, Worker evidence, resolved inputs, produced Artifacts, actions | A second copy of the whole Steps table |
| Header/list/artifact strip | “What is the Run verdict and what can I do now?” | Verdict, failed node, blocked count, final outputs, actions | Per-node forensic detail |

---

## Task 1: Freeze the evidence fixtures and screen contract

**Files:**
- Create: `tests/fixtures/project_run_cases.py`
- Modify: `tests/test_project_runs_gui.py`
- Modify: `tests/test_phase4_api.py`
- Modify: `docs/Relay_GUI_Project_Runs_Screen_Design_v1.0.md` (update the document title/version to v1.1 while retaining the path used by current references)

**Interfaces:**
- Produces deterministic fixtures for `success_parallel`, `schema_failure_blocked`, `unsupported_worker_blocked`, `cancelled`, `awaiting_approval`, and `retry_then_success`.
- Each fixture exposes `catalog_item`, `snapshot`, `steps`, `receipt`, `task_run_details`, and `artifacts` so GUI tests do not depend on mutable Relay Home records.

- [ ] **Step 1: Write fixture tests first.** Assert the fixtures preserve the facts above: six-node successful graph with three final roles; schema failure with one blocked descendant; unsupported Worker with four blocked descendants; cancelled Run with no failure node.
- [ ] **Step 2: Run the fixture tests and confirm they fail because the fixture module does not exist.**
- [ ] **Step 3: Implement only the fixture builders.** Keep IDs deterministic and use the same public keys as `/v1/catalog/project-runs`, `/v1/project-runs/{id}`, `/steps`, `/receipt`, `/v1/jobs/{task_run_id}`, and `/artifacts`.
- [ ] **Step 4: Run the fixture and existing Project Runs tests.** Expected: all fixture assertions pass; no Relay Home data is modified.
- [ ] **Step 5: Update the design document with the final Pipeline/Steps/Inspector contract and the acceptance matrix below.**

Acceptance matrix:

| Case | Pipeline | Steps | Inspector | Header/actions |
|---|---|---|---|---|
| Success | All nodes green, parallel roots visible, final node connected | Defined execution order, actual Workers, all attempts | Selected node evidence | Completed + 3 final Artifacts |
| Schema failure | Failed image node + blocked render node, dashed causal edge | `SCHEMA_MISMATCH`, failed Task Run | Partial output/log/artifact evidence | Failure reason + retry/re-execute |
| Unsupported Worker | First dispatch failure highlighted, descendants blocked | Requested Worker and dispatch error | No fake Worker evidence | Unsupported Worker + blocked count |
| Cancelled | Last reached node and remaining nodes marked cancelled/not-started | Cancellation status and last Task Run | Cancellation reason | Cancelled, no failure wording |

---

## Task 2: Normalize the Project Run detail state and API response adapters

**Files:**
- Modify: `relay/gui/main_window.py:575-605,1865-1885`
- Modify: `relay/gui/project_runs.py:ProjectRunsView, ProjectRunDetailView`
- Test: `tests/test_project_runs_gui.py`
- Test: `tests/test_phase4_api.py` if an API contract is changed

**Interfaces:**
- Add one GUI-side normalization boundary for catalog, detail, steps, receipt, Job detail, and Artifact responses.
- Keep `ProjectRunsView.runs` as the catalog cache and `ProjectRunDetailView._run` as the selected detail cache; never replace a loaded detail field with `None` from a sparse poll.
- Job detail adapter accepts both `{job: {...}}`/`{task_run: {...}}` compatibility responses and the current direct Job detail object.

- [ ] **Step 1: Add failing tests.** Cover out-of-order detail/steps/receipt responses, sparse catalog refresh, direct `/v1/jobs/{id}` response, and a failed request producing a bounded “unavailable; retry” state rather than permanent `Loading…`.
- [ ] **Step 2: Run those tests and confirm the direct-response and sparse-refresh failures.**
- [ ] **Step 3: Implement the adapter and merge rules.** For each field, use “new non-null value wins; sparse null does not erase loaded detail”; retain selected Project Run ID and Inspector open state across terminal refreshes.
- [ ] **Step 4: Add per-section load state:** `not_requested`, `loading`, `loaded`, `unavailable`. Replace indefinite `Loading…` with `Loading…` only while a request is pending and `Unavailable · Retry` after an error or timeout.
- [ ] **Step 5: Run the focused GUI/API tests and verify response order permutations.**

---

## Task 3: Redesign Pipeline as a topology-first graph

**Files:**
- Modify: `relay/gui/project_runs.py:ProjectRunPipelineView, ProjectRunNodeCard, ProjectRunEdgeArrow`
- Test: `tests/test_project_runs_gui.py`
- Test: `tests/test_phase3_gui.py` if shared graph layout behavior is covered there

**Interfaces:**
- Preserve `ProjectRunPipelineView.set_run(project_run_id, snapshot, steps, receipt_steps)` and `node_selected = Signal(str)` so MainWindow wiring remains compatible.
- Replace the current full-grid edge overlay with a graph scene/canvas whose node layout returns explicit rectangles and whose edges connect card centers/borders.
- Preserve scroll/pan and the `node_selected` signal; add a non-emitting `set_selected_node(node_id | None)` API for refresh restoration.

- [ ] **Step 1: Write failing tests for graph geometry.** Assert adjacent levels produce non-zero edge lengths, edges connect the correct node rectangles, parallel branches occupy separate rows, failed→blocked edges are dashed, and a 20-node graph remains reachable through scroll/pan.
- [ ] **Step 2: Run tests and confirm current zero-length/overlapped edge failures.**
- [ ] **Step 3: Implement a topology layout model.** Use snapshot node order plus predecessor depth; assign columns by depth, rows by branch, and compute card rectangles from actual widget sizes instead of the fixed `200` constant.
- [ ] **Step 4: Implement the graph canvas.** Render compact cards with only node ID, Task label, status icon/word, and a retry badge; show an error badge only for failed nodes. Do not repeat full Worker/duration/Task Run detail on every card.
- [ ] **Step 5: Render edges from source right edge to target left edge.** Use solid edges for normal dependencies, dashed edges for failed→blocked causality, and arrowheads that do not cover cards.
- [ ] **Step 6: Add explicit empty/partial graph states.** Distinguish “snapshot unavailable” from “Project has no nodes” and show a retry action for the former.
- [ ] **Step 7: Run Pipeline tests and inspect success, schema failure, unsupported Worker, and 20-node cases in real Qt.**

Pipeline card content after this task:

```text
image_collection
인물 이미지 수집·검증
● Failed
SCHEMA_MISMATCH
```

Worker, duration, attempt table, resolved inputs, and artifact details belong to Steps/Inspector.

---

## Task 4: Make Steps the canonical execution ledger

**Files:**
- Modify: `relay/gui/project_runs.py:ProjectRunDetailView._render_steps`
- Modify: `relay/db.py:2141-2148` to document or implement the stable step ordering contract required by the UI
- Modify: `relay/api.py:1069-1072` to expose the stable step ordering/attempt contract required by the UI
- Test: `tests/test_project_runs_gui.py`
- Test: `tests/test_phase4_api.py`

**Interfaces:**
- Steps table columns: `#`, `Node`, `Status`, `Attempts`, `Started`, `Duration`, `Requested Worker`, `Actual Worker`, `Error`, `Task Run`.
- Sort order: snapshot Project node order first; unknown/legacy nodes by earliest `started_at`, then node ID. Do not sort alphabetically when a snapshot exists.
- Actual Worker comes from Task Run detail/attempt evidence; requested Worker comes from Task snapshot/request; missing data is shown as `—`, never a false Worker.

- [ ] **Step 1: Add failing tests for snapshot order, fallback order, requested→actual Worker display, retry counts, and blocked/no-started-time rows.**
- [ ] **Step 2: Run tests and confirm alphabetical-order and missing-worker failures.**
- [ ] **Step 3: Implement a pure `ordered_steps`/`worker_evidence` view-model helper.** Keep sorting and evidence precedence outside widget painting.
- [ ] **Step 4: Render sequence numbers and execution metadata.** Use `started_at`/`completed_at` for duration; display `Not started` for blocked nodes rather than `—`.
- [ ] **Step 5: Make row selection stable across refreshes by node ID, not row index.**
- [ ] **Step 6: Run Steps/API tests with success, failure, retry, and cancelled fixtures.**

---

## Task 5: Make Inspector an explicit toggleable evidence drawer

**Files:**
- Modify: `relay/gui/project_runs.py:ProjectRunDetailView, ProjectRunInspectorView`
- Modify: `relay/gui/main_window.py` only for normalized node-detail/artifact error handling
- Test: `tests/test_project_runs_gui.py`

**Interfaces:**
- `open_node_inspector(node_id)` and `toggle_node_inspector(node_id)` are the only state-changing entry points.
- Clicking a Pipeline card or Steps row selects that node and opens the drawer; clicking the same Pipeline card again closes it; a refresh does not close an explicitly open drawer.
- The drawer exposes: verdict, attempt history, requested/actual Worker, error/failure reason, resolved inputs, produced Artifacts, logs/answer/re-execute actions, and section-level loading/error states.

- [ ] **Step 1: Add failing tests for open, same-node toggle close, different-node switch, refresh persistence, tab switch persistence, and direct Job detail response.**
- [ ] **Step 2: Run tests and confirm current auto-close/`Loading…` behavior.**
- [ ] **Step 3: Implement a selected-node state object keyed by `(project_run_id, node_id)`.** Do not infer open state from widget visibility alone.
- [ ] **Step 4: Add explicit close affordance and a compact header:** `node · status · attempt count · actual Worker`.
- [ ] **Step 5: Replace permanent Loading labels with section-level loading/error/empty states and retry actions.**
- [ ] **Step 6: Run Inspector tests with schema failure, unsupported Worker, retry-then-success, and missing Artifact cases.**

---

## Task 6: Upgrade Timeline to explain elapsed time and waiting

**Files:**
- Modify: `relay/gui/project_runs.py:ProjectRunTimelineView, ProjectRunTimelineCanvas`
- Modify: `relay/api.py`/`relay/db.py` to expose the stable Run start/attempt fields used by Timeline
- Test: `tests/test_project_runs_gui.py`
- Test: `tests/test_phase4_api.py`

**Interfaces:**
- Timeline consumes Run start/end, step times, `project_step_runs`, and receipt attempts.
- Run start fallback order: persisted `project_runs.started_at`, earliest dispatched step, then `created_at`; show which fallback was used in a tooltip/metadata line.
- Blocked/not-started nodes render a labelled marker, not a zero-width blank row; retries render separate bars with retry gaps.

- [ ] **Step 1: Add failing tests for the successful parallel Run, schema-failure blocked node, retry gaps, cancelled Run, and missing `started_at`.**
- [ ] **Step 2: Run tests and confirm missing-start/blocked-row ambiguity.**
- [ ] **Step 3: Implement the time model and fallback labels.**
- [ ] **Step 4: Add a legend for completed, running, failed, blocked, cancelled, and waiting.**
- [ ] **Step 5: Run timeline tests and inspect the five evidence fixtures in real Qt.**

---

## Task 7: Strengthen list, verdict header, actions, and final artifacts

**Files:**
- Modify: `relay/gui/project_runs.py:ProjectRunsView, ProjectRunDetailView`
- Modify: `relay/api.py:catalog_project_runs` with regression coverage for the existing failure/blocked/final-output fields
- Test: `tests/test_project_runs_gui.py`
- Test: `tests/test_phase4_api.py`

**Interfaces:**
- List row shows status icon/word, Project name, `completed/total`, failed node, blocked count, trigger, and relative time.
- Header verdict is generated from status + failed node + error + blocked count + final Artifact count.
- Actions are state-aware: cancel only live, retry/re-execute only eligible, approve/reject only pending approval, open final Artifact only when available.

- [ ] **Step 1: Add failing tests for all evidence fixtures and terminal polling behavior.**
- [ ] **Step 2: Verify existing catalog fields (`failed_node_id`, `blocked_step_count`, final roles) before adding API work.**
- [ ] **Step 3: Implement compact verdict/list rendering and explicit unavailable states.**
- [ ] **Step 4: Make final Artifact strip role-first:** `final_report`, `report_json`, `assets_bundle`, with size/path/status and open action.
- [ ] **Step 5: Add a “last refreshed” indicator and preserve selection/filter/tree expansion through catalog refreshes.**
- [ ] **Step 6: Run list/header/action tests with success, failure, cancelled, and awaiting-approval fixtures.**

---

## Task 8: End-to-end real-Qt acceptance and documentation

**Files:**
- Modify: `tests/test_project_runs_gui.py` only for final regression coverage
- Modify: `docs/Relay_GUI_Project_Runs_Screen_Design_v1.0.md` (the v1.1 content remains at this path)
- Modify: `memo.md`, `log.md`, and the owning wiki page if durable behavior changes

**Interfaces:**
- No new product behavior; this task verifies the complete screen against the fixture matrix and real registered Runs.

- [ ] **Step 1: Run the full suite.**

```bash
python -m unittest discover -s tests
```

Expected: zero failures; record the exact test count.

- [ ] **Step 2: Run focused static checks.**

```bash
python -m ruff check relay/gui/project_runs.py relay/gui/main_window.py relay/api.py relay/db.py tests/test_project_runs_gui.py tests/test_phase4_api.py
git diff --check
```

- [ ] **Step 3: Start the GUI with the real Qt platform and inspect each fixture at 1280×720 and 1024×700.** Verify Pipeline scroll, edge placement, Steps order/Worker, Inspector toggle/loading, Timeline blocked/retry states, final Artifacts, and terminal refresh stability.
- [ ] **Step 4: Verify actual Relay Home cases without mutating them:** successful `[L1]` Run, schema failure, unsupported Worker failures, and cancelled Run.
- [ ] **Step 5: Update the design document acceptance criteria and memory.** Remove resolved items from `memo.md`; retain only genuine remaining defects such as any edge geometry issue not fixed by Task 3.
- [ ] **Step 6: Record the completed outcome in `log.md` and report remaining gaps explicitly.**

## Self-review checklist

- [ ] Pipeline and Steps have non-overlapping primary information.
- [ ] Success, schema-failure, unsupported-Worker, cancelled, and retry-then-success cases are all represented in tests.
- [ ] No UI section can remain indefinitely in `Loading…` after a failed request.
- [ ] A selected Inspector survives refresh and closes only through its explicit toggle/close action.
- [ ] Pipeline edges have real geometry and 20-node graphs remain usable.
- [ ] Steps are deterministic for parallel tasks and chronological for legacy/no-snapshot data.
- [ ] Actual Worker evidence is displayed without inventing a Worker when unavailable.
- [ ] Full tests, Ruff, git diff check, and real-Qt visual acceptance are all complete.
