# Relay GUI Design Instruction

> **Status:** Foundation implemented; incremental screen rollout remains
> **Scope:** Relay desktop GUI (PySide6), all current and future screens
> **Visual references:** `docs/a1.png`, `docs/a2.png`
> **Product basis:** `docs/Relay_Product_Identity_v1.0.md`

## 1. Product stance

Relay is not a chat client or a generic AI dashboard. It is a workspace for
designing work, delegating it to Workers, inspecting outputs, and making
decisions at checkpoints. The GUI must therefore make the following questions
easy to answer before it makes the interface decorative:

1. What work is running, waiting, failed, or needs a human decision?
2. Which Worker, Task, input Artifact, and Project step produced this state?
3. What is the next safe action?

The visual direction is **Relay Operations Studio**: a dark, calm, precise
control surface. It borrows the reference images' layered dark surfaces,
structured sidebar, dense-but-readable operational tables, restrained glow,
and graph/log affordances. It must not copy their fictional data, layout
literally, or turn every panel into a neon card.

### Design principles

- **Work first, chrome second.** Content and decision state take priority over
  decoration, gradients, or empty dashboard metrics.
- **Progressive disclosure.** Show a concise operational summary first; open
  input manifests, logs, JSON, lineage, and raw receipts only on demand.
- **One visual grammar.** Task, Run, Project, Routine, Approval, and future
  entities use the same status badge, metadata row, card, table, toolbar, and
  empty-state rules.
- **State is redundant.** Never communicate success, warning, failure, or
  selection with color alone. Pair color with a label, icon, shape, or text.
- **Calm density.** Prefer compact operational density with clear grouping over
  oversized consumer-app whitespace. Preserve readable line height and stable
  column alignment.
- **Trust through restraint.** Bright cyan, green, amber, and red are reserved
  for actionable state. Most of the application is neutral slate.

## 2. Visual language

### 2.1 Color tokens

All GUI code must consume named design tokens rather than inline hex colors.
The exact values below are the first dark-theme palette; semantic names are the
contract, so accessible values can evolve without changing each screen.

| Token | Initial value | Use |
|---|---:|---|
| `bg.canvas` | `#0F172A` | application background and graph canvas |
| `bg.sidebar` | `#111C2E` | persistent navigation rail |
| `bg.topbar` | `#162238` | page title / global action bar |
| `bg.surface` | `#1B2A40` | cards, tables, inspector panels |
| `bg.surfaceRaised` | `#263A55` | hover, selected rows, secondary cards |
| `bg.input` | `#121F33` | editable controls and code/log panes |
| `border.subtle` | `#354A66` | panel and table separation |
| `border.focus` | `#6DD6F7` | keyboard focus and selected graph node |
| `text.primary` | `#F3F7FC` | titles and primary values |
| `text.secondary` | `#C3D0E0` | metadata and labels |
| `text.muted` | `#9AAAC0` | helper text, placeholders, timestamps |
| `accent.primary` | `#1769AA` | primary action and active navigation |
| `accent.cyan` | `#6DD6F7` | live route, selected node, link affordance |
| `state.success` | `#70E0A0` | completed, healthy, available |
| `state.warning` | `#FFD166` | queued, attention, partial, caution |
| `state.danger` | `#FF8A8F` | failed, blocked, destructive action |
| `state.info` | `#9BB8FF` | running and informational status |

Rules:

- Use a subtle surface tint and border for state; do not fill large panels with
  saturated state colors.
- `accent.cyan` glow is allowed only for an actively selected graph route,
  currently running Worker, or focused keyboard control. It is never a default
  card shadow.
- Error details use a dark red surface with readable light text, not bright red
  full-screen banners.
- Theme support starts dark-first. A future light theme must map the same
  semantic tokens; no screen may assume a literal dark hex value.

### 2.2 Typography, spacing, and shape

| Token | Rule |
|---|---|
| Font | System UI font; monospaced system font only for IDs, commands, JSON, diffs, and logs. |
| `type.pageTitle` | 22–24 px, semibold; one per page. |
| `type.sectionTitle` | 14–16 px, semibold, uppercase only for small operational group labels. |
| `type.body` | 13–14 px, regular. |
| `type.meta` | 11–12 px; secondary color. |
| Spacing scale | 4, 8, 12, 16, 24, 32 px only. |
| Radius | 6 px for controls and badges; 10 px for cards/panels; no pill-shaped containers except compact badges. |
| Borders | 1 px subtle border; avoid arbitrary divider colors. |
| Elevation | Prefer border + surface contrast; use shadow only for modal dialogs and floating inspector panels. |

## 3. Application shell

The shell is persistent so every future screen inherits the same orientation.

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Brand / page title          global search     health   attention  user │
├───────────────┬──────────────────────────────────────────────────────┤
│ Logo          │ Page toolbar: title, scope, filters, primary action  │
│ Dashboard     ├──────────────────────────────────────────────────────┤
│ Runs          │                                                      │
│ Tasks         │                    Page content                      │
│ Projects      │                                                      │
│ Routines      │                                                      │
│ Attention     │                                                      │
│ Operations    │                                                      │
│ Logs          │                                                      │
│ Settings      │                                                      │
├───────────────┴──────────────────────────────────────────────────────┤
│ Connection / selected Relay Home / concise operation feedback         │
└──────────────────────────────────────────────────────────────────────┘
```

### Navigation rules

- The left rail is the only primary navigation. It contains icon + label, a
  single clear selected state, and count badges only when the count is
  actionable (for example pending approvals).
- `Runs`, `Tasks`, `Projects`, and `Routines` are peer work objects. Do not
  hide them inside Settings or a generic sidebar list.
- Schedules stay inside the Runs context until a deliberate Schedule/Routine
  consolidation is designed. Do not imply that a Schedule was migrated to a
  Routine.
- Global search is for locating known work. Page-local filters stay in the
  page toolbar; do not place four unrelated filters inside the navigation rail.
- The top-right health indicator is a compact status control. It opens detail
  on click; it is not a permanent multi-line banner.

### Responsive desktop behavior

- Baseline target is 1280×720; panels must remain usable down to 1024×700.
- Below the comfortable two-column threshold, inspector panels collapse into a
  tab or drawer. Never make a table horizontally scroll solely to preserve a
  decorative chart.
- Persist only presentation preferences: window size, splitter positions,
  current section, and non-sensitive filters. Never persist secrets, form
  contents, or domain data in `gui.ini`.

## 4. Shared component grammar

### 4.1 Page header and toolbar

Every top-level screen uses this order:

1. page title and one-line purpose;
2. optional scope/count summary;
3. local search and filters;
4. one primary action (blue) at the right;
5. secondary actions as quiet outline/icon buttons.

Examples: `+ New Task`, `+ New Project`, `Run now`, `Approve`, and
`Export`. A destructive action never occupies the primary position.

### 4.2 Status badges

Use the shared `StatusBadge` for all entity states. It contains a small status
dot/icon plus text, uses a neutral-dark background with a semantic border, and
has a stable minimum width. Suggested labels:

| Meaning | Display label | Semantic color |
|---|---|---|
| active execution | `Running` / `Processing` | info |
| accepted work | `Queued` | warning |
| successful output | `Completed` | success |
| incomplete output | `Partial` | warning |
| requires decision | `Needs approval` | warning |
| failure | `Failed` | danger |
| intentional stop | `Cancelled` | muted |
| unavailable Worker | `Unavailable` | muted/danger |

Do not create screen-specific status color maps.

### 4.3 Cards, tables, and empty states

- **Metric cards** contain one value, one label, and one short qualifier. They
  are for operational summaries, not duplicated decorative counters.
- **Entity cards** contain title, one-line description, status, compact tags,
  and the next action. Cards are used for browse/select views; tables are used
  when users compare multiple fields.
- **Operational tables** use a compact header, aligned status column, stable
  row height, hover/selection state, and an overflow action menu. IDs are
  monospaced and truncated with copy-on-click or an explicit copy control.
- **Empty states** explain why the area is empty and offer one safe next action.
  Example: “No Project Runs yet — run this Project to create the first
  traceable execution.”

### 4.4 Detail, inspector, and evidence views

The standard detail layout is:

```text
Identity + status + primary action
Summary cards / decision-critical metadata
Tabs: Overview | Inputs | Outputs | Lineage | Activity | Raw evidence
```

- `Overview` is readable prose and key-value metadata.
- `Inputs` and `Outputs` use Artifact rows with role, source, hash state, and
  open/copy actions.
- `Lineage` uses a compact graph/table hybrid; it never displays an arbitrary
  file path as an input guarantee.
- `Activity` contains bounded events and status transitions.
- `Raw evidence` is monospaced, read-only, copyable, and visually separated
  from user-facing results.

### 4.5 Forms and dialogs

- Group a form into `Identity`, `Execution`, `Inputs`, `Outputs`, and
  `Advanced` sections. Advanced controls are collapsed by default only when
  they are safe to defer.
- Put field help beneath the relevant control, not in a remote tooltip alone.
- Validate locally for shape (required fields, JSON syntax, duplicate aliases)
  and let the daemon remain authoritative for domain validation.
- Preserve user input after a daemon validation error; show code + human
  message in an inline error panel.
- Dialog footer order: quiet `Cancel`, then the single primary submit action.
  Reject/delete/overwrite requires an explicit confirmation dialog naming the
  affected object and preserved history.

## 5. Screen-level direction

### Dashboard

The Dashboard is an operational snapshot, not a vanity analytics screen.

- Top row: active queue count, Workers available, pending approvals, failure
  count in the chosen period.
- Main row: active Task Run table and a compact Project delegation/lineage
  panel. The latter shows real Project nodes and status, never fictional flow
  data.
- Bottom row: success/failure trend only if enough history exists; otherwise
  use a meaningful empty state. A bounded execution-log monitor is optional
  and must link to the real Run detail.

### Runs

- Keep the existing active/finished hierarchy, but move search/filter controls
  to the page toolbar and use shared status badges.
- The Run detail becomes the canonical evidence view for Task, Artifact,
  lineage, quality, receipt, result, logs, and events.
- Show the next safe action first: cancel a live Run, inspect a failed Run,
  approve a checkpoint, compare a completed Run, or save it as a Task.

### Tasks

- Browse with entity cards or a compact table: name, version, default Worker,
  last Run, and latest result state.
- Detail uses the shared evidence tabs and makes immutable historical snapshots
  explicit: editing a Task changes future Runs only.

### Projects

- Use a three-part workspace: Project list, deterministic flow preview, and
  properties/Run inspector. The existing structured editor remains the source
  of truth; a free-form drag canvas is not required in the first redesign.
- Flow nodes represent Tasks and checkpoints; edges represent Artifact role to
  alias handoff. Node border/status communicates execution state.
- Run monitoring uses the same node grammar with live state, failure cause,
  approval pause, and child Run links.

### Routines and Schedules

- Show target, enabled state, next occurrence, last outcome, and overlap/missed
  policy as compact tags.
- Make `Run now`, pause/resume, preview, and history visible without conflating
  legacy Schedules with Routines.

### Attention, Logs, Settings, and future screens

- Attention is an action inbox: failed work, low quality, and pending approval
  are sorted by urgency and route to the owning detail view.
- Logs are a troubleshooting workspace with severity badge, time range,
  structured row list, and a right-side error inspector.
- Settings keeps safety-sensitive controls visually separated from ordinary
  preferences and always explains immediate daemon impact.
- Any future screen begins with the shell, page header, component grammar, and
  evidence hierarchy in this document. It may introduce a new component only
  after documenting why `Card`, `Table`, `Detail`, `Inspector`, or `StatusBadge`
  cannot express the need.

## 6. PySide6 implementation architecture

Create a small, explicit GUI design layer before restyling individual screens.

| Module | Responsibility |
|---|---|
| `relay/gui/design_tokens.py` | Named colors, spacing, radii, typography, status metadata, and theme lookup. No widgets. |
| `relay/gui/design_styles.py` | One application stylesheet generated from tokens for `QApplication`, controls, tables, tabs, scrollbars, and focus states. |
| `relay/gui/design_widgets.py` | Reusable `StatusBadge`, `SectionHeader`, `MetricCard`, `EmptyState`, `InlineNotice`, `ToolbarButton`, and metadata-row widgets. |
| `relay/gui/main_window.py` | Shell composition, navigation routing, global health/attention state. It must not own per-domain widget styling. |
| `relay/gui/tasks.py`, `projects.py`, `routines.py`, `job_detail.py`, future views | Compose shared components and emit domain signals; no copied palette literals or ad-hoc status maps. |
| `tests/test_gui_design_system.py` | Offscreen tests for token use, badge semantics, focus visibility, shell navigation, and representative empty/error states. |

Implementation rules:

- Apply the global stylesheet once in `relay/gui/app.py` immediately after
  `QApplication` creation.
- Assign stable `objectName` values only to semantic component variants, such
  as `primaryAction`, `dangerAction`, `sidebarNav`, `codePane`, and
  `statusBadge`. Do not style based on visible text.
- Replace existing `setStyleSheet()` one-offs incrementally. A local one-off is
  allowed only for dynamic state that is not representable through a property;
  it must read token values rather than literal hex strings.
- Use Qt dynamic properties (`state=running`, `tone=danger`,
  `selected=true`) for semantic variants and repolish the widget after changes.
- Keep GUI transport unchanged: domain widgets render API payloads and emit
  signals; `MainWindow` remains responsible for `GuiRpcClient` request routing.

## 7. Delivery plan

### Phase A — Foundation and shell

1. Add tokens, stylesheet, and shared components with offscreen tests.
2. Apply the dark theme in `app.py`; ensure disabled, hover, selected, and
   keyboard-focus states remain distinguishable.
3. Restructure `MainWindow` into top bar, persistent navigation rail, page
   header slot, content stack, and concise status bar.
4. Move hard-coded colors and duplicate status maps into shared components.
5. Fix visible structural defects encountered while touching the views, such as
   duplicate signal emission or duplicated unreachable code; add a focused
   regression before correcting each defect.

### Phase B — High-frequency work surfaces

1. Redesign Runs and Task Run detail using shared status badges, evidence tabs,
   Artifact rows, and action hierarchy.
2. Redesign Task browse/detail/editor using the list-detail pattern.
3. Redesign Project browse/detail/flow preview and Project Run monitor.
4. Redesign Routine/Schedule browse/detail/editor while retaining their
   different domain meanings.

### Phase C — Operations surfaces

1. Add Dashboard, Attention, and Logs with real daemon data and explicit empty
   states.
2. Add comparison, quality, notification, export/import, and receipt actions
   to their owning details rather than creating an unrelated button collection.
3. Add dashboard charts only after the corresponding API provides meaningful
   historical data; use tables and summaries first.

### Phase D — Quality gates and rollout

1. Add offscreen tests for every shared component state and every top-level
   page's primary/disabled/empty/error path.
2. Capture deterministic GUI screenshots at 1280×720 for Dashboard, Runs,
   Tasks, Projects, Routines, Attention, Logs, and Settings. Review them as a
   set so a new screen cannot drift from the grammar.
3. Run the full unit suite, GUI smoke tests, Ruff check/format, compileall, and
   release build on the supported platforms.
4. Do not alter API schema revision or GUI compatibility floor for a visual-only
   redesign. Document any unavoidable contract addition separately.

## 8. Non-goals and guardrails

- No generic chat transcript as the primary application view.
- No fake analytics, placeholder Worker data, or simulated graph activity in
  production screens.
- No free-form graph editor persistence until Project definitions support its
  geometry as a real product requirement.
- No visual-only status indication, inaccessible focus state, low-contrast
  muted text, or color-dependent error meaning.
- No direct SQLite reads from GUI widgets and no GUI-side workaround for daemon
  contract errors.
- No broad refactor of daemon/domain behavior as part of styling; contract
  repairs must be small, tested, and explicitly justified by a GUI flow.

## 9. Definition of done

The redesign is complete only when a first-time user can identify current work,
find a failed or approval-blocked Run, inspect its evidence, and take the next
safe action without reading raw logs; and when a maintainer can add a new
screen by composing the shared shell/components without inventing colors,
spacing, status semantics, or page structure.
