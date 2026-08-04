# GUI User Scenario Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every visible Relay GUI action demonstrably usable against a
compatible daemon, and make incompatible-daemon states explicit and safely
recoverable rather than appearing as unresponsive controls.

**Architecture:** Keep domain behavior behind the authenticated daemon API.
Add a small Qt user-action test driver for real click/signal/navigation checks,
then exercise it against both deterministic widget fixtures and a temporary
source daemon. Retain one manual release checklist for desktop-specific
rendering, keyboard focus, and OS window behavior that offscreen Qt cannot
prove.

**Tech Stack:** Python 3.11+, PySide6 (`QtTest.QTest`), `unittest`, temporary
Relay Home, `RelayDaemon`, `RPCClient`, Ruff.

## Global Constraints

- GUI widgets never read SQLite directly; all domain operations use
  `GuiRpcClient` and daemon routes.
- Preserve Relay Home data, active Jobs, Artifacts, Schedules, Routines, and
  daemon authentication material. Compatibility recovery must never silently
  stop or replace a daemon with active work.
- API schema revision remains 5 and minimum GUI remains 1.1.0 unless a
  separately approved contract changes it.
- Tests run with `QT_QPA_PLATFORM=offscreen`; human visual review remains
  required at 1280×720 and 1024×700.
- A disabled action must expose a visible reason and a safe next action. Never
  rely on a disabled button alone to communicate daemon incompatibility.
- Preserve the semantic GUI design system in `docs/design_inst.md`; tests use
  object names and behaviour, not literal pixel colors.

---

## Evidence from the current inspection

The reported `+ New Task` failure was reproduced without changing application
code on 2026-08-04:

```text
Relay Home: /home/doyoonkim/.relay
daemon status: running
GUI mode: read-only
New Task enabled: false
Health: Health: Compatibility warning
Banner: Read-only compatibility mode: daemon does not support the required API
```

`MainWindow._set_connection()` disables `new_task_button` whenever the
compatibility decision is not `normal`. The source GUI connected to the older
installed `relay.pyz` daemon rather than a source-compatible daemon. The
existing `tests/test_g1_gui.py` tests the normal path and direct
`_show_new_task()` method call, but not a real click after each connection
state or the source-versus-installed-daemon mismatch.

The first implementation task below addresses this proven gap; the rest builds
the repeatable user-scenario suite around it.

## User scenario inventory

| ID | User action | Setup | Expected observable result | Automation layer |
|---|---|---|---|---|
| L-01 | Launch GUI with source-compatible daemon | Fresh temporary Home | Health becomes Healthy; create actions enable | daemon integration |
| L-02 | Launch GUI with no daemon and auto-start enabled | Fresh temporary Home | Source daemon starts; health becomes Healthy | daemon integration |
| L-03 | Launch GUI with incompatible daemon | Existing daemon advertises unsupported API | Compatibility banner names the cause; unsafe actions are disabled; recovery guidance is actionable | widget + daemon integration |
| L-04 | Click Refresh health after a daemon state change | Window already open | Label, timestamp, banner, and action enabled state refresh together | daemon integration |
| R-01 | Click Runs | Any GUI state | Runs nav is selected and Run list remains available | widget |
| R-02 | Click `+ New Task` | Normal mode | New Task view is the current stack widget; page context is clear | widget + daemon integration |
| R-03 | Submit a valid Task Run | Normal mode; mock Worker | One request is sent, submit control prevents duplicate submission, Run becomes selectable | daemon integration |
| R-04 | Submit invalid/missing task text | New Task view | Inline validation identifies the field and preserves inputs | widget |
| R-05 | Open completed Run and inspect tabs | Completed fixture Run | Overview, Inputs, Result, Logs, and Events load; copy/open actions honor availability | daemon integration |
| T-01 | Open Tasks and filter | Two registered Tasks | Filter updates list and count; clearing filter restores rows | widget |
| T-02 | Create, edit, run, and delete Task | Compatible daemon | Version increments on edit; old Run retains snapshot; delete preserves history | daemon integration |
| P-01 | Create Project DAG and start Project Run | Two registered Tasks | Invalid cycle/alias is rejected locally; valid Run opens monitor | widget + daemon integration |
| P-02 | Inspect Project Run | Project fixture with completed/failed steps | Node state, child Run link, retry/cancel affordances agree with daemon actions | daemon integration |
| U-01 | Create Routine and preview occurrences | Compatible daemon | Preview renders timezone-aware occurrences before save | widget + daemon integration |
| U-02 | Run/pause/resume Routine | Existing Routine | State and history refresh; Schedule remains a separate legacy entity | daemon integration |
| S-01 | Open Schedule and Run now | Existing Schedule | Detail opens and daemon receives only the selected Schedule action | daemon integration |
| G-01 | Keyboard traversal | 1280×720 window | Focus is visible; Tab reaches health, New Task, nav, filters, list, detail actions | manual screenshot + Qt focus test |
| G-02 | Narrow desktop layout | 1024×700 window | No primary action is clipped; splitter/detail remain usable | manual screenshot |
| E-01 | Daemon timeout/route error during each mutation | Controlled error response | Initiating control recovers, content remains, error code/message is visible | widget + daemon integration |

## File structure

| File | Responsibility |
|---|---|
| `relay/gui/app.py` | Translate startup compatibility state into a clear, safe GUI entry path. |
| `relay/gui/main_window.py` | Expose connection state, selected stack widget, and disabled-action explanation through existing banner/health controls. |
| `relay/gui/qa.py` | Small test-only-safe helper functions for waiting for GUI conditions and inspecting enabled/visible action state. No domain logic. |
| `tests/test_gui_user_scenarios.py` | Click-driven, deterministic widget scenarios for launch state, navigation, New Task, errors, and focus. |
| `tests/test_gui_daemon_scenarios.py` | Temporary-Home daemon scenarios using a mock Worker and real authenticated GUI requests. |
| `tests/test_g1_gui.py` | Keep legacy GUI coverage; add only focused regression assertions that belong to its existing real-daemon fixture. |
| `docs/Relay_GUI_Manual_Validation_v1.0.md` | Versioned human release checklist with screenshots and expected state. |
| `docs/design_inst.md` | Link the design-system definition of done to the automated and manual validation gates. |

## Task 1: Make compatibility-disabled actions explain themselves

**Files:**

- Modify: `relay/gui/main_window.py:1585-1620`
- Modify: `relay/gui/design_widgets.py`
- Test: `tests/test_gui_user_scenarios.py`

**Consumes:** `MainWindow._set_connection(mode, reason, health=None)` and the
existing `InlineNotice` design widget.

**Produces:** `MainWindow._set_action_availability(mode, reason)` which sets
enabled state, tooltip, accessible description, and the visible compatibility
notice for all actions that require a normal daemon.

- [ ] **Step 1: Write the failing click-state regression.**

```python
def test_incompatible_daemon_explains_why_new_task_cannot_open(self):
    window = self.build_window()
    window._set_connection("read-only", "daemon does not support the required API")

    self.assertFalse(window.new_task_button.isEnabled())
    self.assertIn("required API", window.new_task_button.toolTip())
    self.assertIn("required API", window.banner.text())
```

- [ ] **Step 2: Run the failing regression.**

Run: `python -m unittest tests.test_gui_user_scenarios.GuiLaunchScenarioTests.test_incompatible_daemon_explains_why_new_task_cannot_open`

Expected: FAIL because the disabled button has no explanation.

- [ ] **Step 3: Implement the smallest availability helper.**

```python
def _set_action_availability(self, mode: str, reason: str | None) -> None:
    enabled = mode == "normal"
    explanation = "" if enabled else (reason or "Relay daemon compatibility is unavailable")
    for action in (self.new_task_button, self.new_task_view.create_button):
        action.setEnabled(enabled)
        action.setToolTip(explanation)
        action.setAccessibleDescription(explanation)
```

Call it from `_set_connection()` before updating the banner. Do not enable
mutations in read-only mode and do not add an automatic daemon restart here.

- [ ] **Step 4: Verify the focused suite.**

Run: `python -m unittest tests.test_gui_user_scenarios tests.test_g1_gui`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the isolated behavior change.**

```bash
git add relay/gui/main_window.py relay/gui/design_widgets.py tests/test_gui_user_scenarios.py
git commit -m "fix: explain disabled GUI actions in compatibility mode"
```

## Task 2: Add click-driven shell and New Task scenarios

**Files:**

- Create: `relay/gui/qa.py`
- Modify: `relay/gui/main_window.py:466-506`
- Test: `tests/test_gui_user_scenarios.py`

**Consumes:** `QTest.mouseClick`, `QApplication.processEvents`, and
`MainWindow.detail_stack`.

**Produces:** `wait_until(predicate, timeout_ms=1500)` and click-level proof
that `+ New Task`, Runs, and Tasks route to the expected current widget.

- [ ] **Step 1: Write failing real-click tests.**

```python
def test_clicking_new_task_opens_the_new_task_view_in_normal_mode(self):
    window = self.build_window()
    window._set_connection("normal", health={})

    QTest.mouseClick(window.new_task_button, Qt.LeftButton)

    self.assertIs(window.detail_stack.currentWidget(), window.new_task_view)
    self.assertEqual(window.detail_view_mode, "new_task")

def test_clicking_tasks_then_runs_updates_the_current_context(self):
    window = self.build_window()
    QTest.mouseClick(window.tasks_button, Qt.LeftButton)
    self.assertTrue(window.tasks_button.isChecked())
    QTest.mouseClick(window.runs_button, Qt.LeftButton)
    self.assertTrue(window.runs_button.isChecked())
```

- [ ] **Step 2: Run the new test module.**

Run: `python -m unittest tests.test_gui_user_scenarios.GuiNavigationScenarioTests`

Expected: FAIL until the helper processes the Qt event queue and every clicked
route has an explicit current page state.

- [ ] **Step 3: Add only deterministic test support.**

```python
def wait_until(predicate, timeout_ms: int = 1500) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return bool(predicate())
```

Use it in tests after clicks and asynchronous transitions. Do not add test
branches, timers, or mock behavior to production widgets.

- [ ] **Step 4: Verify GUI navigation coverage.**

Run: `python -m unittest tests.test_gui_user_scenarios tests/test_gui_design_system.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the scenario harness.**

```bash
git add relay/gui/qa.py relay/gui/main_window.py tests/test_gui_user_scenarios.py
git commit -m "test: cover GUI shell actions with real clicks"
```

## Task 3: Test source daemon startup and incompatible-daemon recovery

**Files:**

- Modify: `relay/cli.py:1850-1857`
- Modify: `relay/gui/app.py:13-25`
- Test: `tests/test_gui_daemon_scenarios.py`
- Test: `tests/test_g1_gui.py`

**Consumes:** `_ensure_daemon(config)`, `RPCClient.health()`,
`evaluate_compatibility()`, daemon PID metadata, and existing authenticated
health route.

**Produces:** `DaemonStartupResult` containing `client`, `started_by_current_process`,
and compatibility metadata; a non-destructive GUI recovery action that can
restart only an idle daemon launched from a mismatched Relay executable.

- [ ] **Step 1: Write failing source/installed mismatch tests.**

```python
def test_gui_marks_a_running_incompatible_daemon_read_only_with_restart_guidance(self):
    daemon = self.start_daemon(api_schema_revision=4, active_jobs=[])
    window = self.open_window_for(daemon.config)

    self.assertEqual(window.current_mode, "read-only")
    self.assertIn("Restart", window.banner.text())
    self.assertFalse(window.new_task_button.isEnabled())

def test_gui_never_offers_restart_while_daemon_has_active_jobs(self):
    daemon = self.start_daemon(api_schema_revision=4, active_jobs=["running-1"])
    window = self.open_window_for(daemon.config)

    self.assertNotIn("Restart daemon", window.banner.text())
```

- [ ] **Step 2: Run the mismatch tests.**

Run: `python -m unittest tests.test_gui_daemon_scenarios.DaemonCompatibilityScenarioTests`

Expected: FAIL because current startup merely accepts any healthy daemon and
the GUI gives no safe recovery action.

- [ ] **Step 3: Implement a guarded recovery path.**

```python
def can_offer_daemon_restart(health: dict, *, current_executable: str) -> bool:
    return (
        health.get("active_job_count", 0) == 0
        and health.get("daemon_executable")
        and health["daemon_executable"] != current_executable
    )
```

Extend `/health` only with additive metadata needed above. Show `Restart
compatible daemon` only when `can_offer_daemon_restart()` is true. Its
confirmation dialog must state that it stops the currently idle daemon, starts
the current executable, and waits for a healthy schema-5 response. If active
Jobs exist, show the incompatible reason and a command/instruction to wait for
or stop work manually; never kill it.

- [ ] **Step 4: Verify daemon and GUI scenarios.**

Run: `python -m unittest tests.test_gui_daemon_scenarios tests.test_g1_gui tests.test_g4_autostart`

Expected: all selected tests pass, including the active-Job safety guard.

- [ ] **Step 5: Commit the compatibility recovery slice.**

```bash
git add relay/cli.py relay/gui/app.py relay/gui/main_window.py tests/test_gui_daemon_scenarios.py tests/test_g1_gui.py
git commit -m "fix: guide GUI recovery from incompatible daemon"
```

## Task 4: Cover Task, Project, Routine, and Schedule mutation flows

**Files:**

- Modify: `tests/test_phase3_gui.py`
- Modify: `tests/test_phase4_gui.py`
- Modify: `tests/test_phase5_gui.py`
- Create: `tests/test_gui_daemon_scenarios.py`

**Consumes:** Current domain widgets and the real daemon route contracts.

**Produces:** One click-driven happy path and one daemon-error/preserved-input
path for each mutable domain object.

- [ ] **Step 1: Write failing mutation scenarios.**

```python
def test_task_editor_keeps_fields_after_daemon_validation_error(self):
    view = TasksView()
    view.show_create_editor()
    view.editor.name_edit.setText("Quarterly report")
    view.editor.instructions_edit.setPlainText("Summarize results")
    view.editor.show_error("TASK_NAME_CONFLICT: name is already registered")

    self.assertEqual(view.editor.name_edit.text(), "Quarterly report")
    self.assertIn("TASK_NAME_CONFLICT", view.editor.error_label.text())

def test_project_run_reexecute_uses_a_real_click(self):
    monitor = ProjectRunMonitorDialog(
        project_run_id="pr-1",
        project_run={"status": "failed", "trigger_type": "manual"},
        steps=[{"node_id": "collect", "task_id": "task-1", "status": "failed"}],
        nodes=[{"node_id": "collect"}],
    )
    received = []
    monitor.accepted_action.connect(lambda action, payload: received.append((action, payload)))
    monitor.reexec_node_edit.setText("collect")

    QTest.mouseClick(monitor.reexec_button, Qt.LeftButton)

    self.assertEqual(
        received,
        [("partial-reexecute", {"project_run_id": "pr-1", "from_node": "collect", "cascade": True})],
    )
```

Use concrete fixture IDs and actual widgets; do not test only internal helper
methods where a user-facing control exists.

- [ ] **Step 2: Run each focused phase suite before implementation.**

Run: `python -m unittest tests.test_phase3_gui tests.test_phase4_gui tests.test_phase5_gui`

Expected: new scenarios fail for missing click coverage or missing preserved
error presentation.

- [ ] **Step 3: Make minimal per-widget corrections.**

Add object names only for semantic controls that need stable lookup, for
example `taskEditorSave`, `projectRunNode:collect`, and `routineRunNow`.
Connect existing signals; do not add a second transport path or duplicate a
daemon route in a widget.

- [ ] **Step 4: Verify all mutable-flow tests.**

Run: `python -m unittest tests.test_phase3_gui tests.test_phase4_gui tests.test_phase5_gui tests.test_gui_daemon_scenarios`

Expected: all selected tests pass.

- [ ] **Step 5: Commit domain scenario coverage.**

```bash
git add relay/gui/tasks.py relay/gui/projects.py relay/gui/routines.py tests/test_phase3_gui.py tests/test_phase4_gui.py tests/test_phase5_gui.py tests/test_gui_daemon_scenarios.py
git commit -m "test: exercise GUI domain mutation scenarios"
```

## Task 5: Add a manual visual and accessibility release gate

**Files:**

- Create: `docs/Relay_GUI_Manual_Validation_v1.0.md`
- Modify: `docs/design_inst.md`
- Test: `tests/test_gui_user_scenarios.py`

**Consumes:** The automated scenario IDs in this plan and the design-system
tokens/components.

**Produces:** A screenshot-backed release checklist with a machine-checkable
mapping from top-level screen to scenario ID.

- [ ] **Step 1: Add a failing completeness test.**

```python
def test_manual_validation_document_covers_every_top_level_navigation_target(self):
    text = Path("docs/Relay_GUI_Manual_Validation_v1.0.md").read_text(encoding="utf-8")
    for section in ("Runs", "Tasks", "Projects", "Routines", "Settings"):
        self.assertIn(f"## {section}", text)
```

- [ ] **Step 2: Run the documentation completeness test.**

Run: `python -m unittest tests.test_gui_user_scenarios.GuiManualGateTests`

Expected: FAIL because the manual validation document does not exist.

- [ ] **Step 3: Create the release checklist.**

For each section, include: launch state, normal-mode action, read-only-mode
action, empty state, error state, Tab/Shift+Tab focus path, 1280×720 screenshot
name, and 1024×700 screenshot name. Require a reviewer to record OS, Python,
Relay version, daemon version/schema, and screenshot paths. Add a link from
`docs/design_inst.md` section 9 to this checklist.

- [ ] **Step 4: Verify the manual-gate test and docs formatting.**

Run: `python -m unittest tests.test_gui_user_scenarios.GuiManualGateTests && git diff --check`

Expected: pass with no whitespace errors.

- [ ] **Step 5: Commit the release gate.**

```bash
git add docs/Relay_GUI_Manual_Validation_v1.0.md docs/design_inst.md tests/test_gui_user_scenarios.py
git commit -m "docs: add GUI manual validation release gate"
```

## Task 6: Run the final verification matrix

**Files:**

- Modify: `log.md`
- Modify: `memo.md`

**Consumes:** All scenario tests and manual screenshot evidence.

**Produces:** Evidence-backed handoff and an updated unresolved-items list.

- [ ] **Step 1: Run all automated GUI scenarios.**

Run: `python -m unittest tests.test_gui_user_scenarios tests.test_gui_daemon_scenarios tests.test_g1_gui tests.test_phase3_gui tests.test_phase4_gui tests.test_phase5_gui`

Expected: zero failures.

- [ ] **Step 2: Run repository verification.**

Run: `ruff check . && ruff format --check . && python -m unittest discover -s tests && python build_release.py && python relay.pyz version && git diff --check`

Expected: every command exits 0. If an existing repository-wide formatter
failure remains, list exact files in `memo.md` and do not claim release
acceptance.

- [ ] **Step 3: Run the manual release checklist.**

Start: `python -m relay --gui`

Record the expected screenshots and outcomes in
`docs/Relay_GUI_Manual_Validation_v1.0.md`. Test both a compatible temporary
source daemon and the real installed Relay Home; record any retained
incompatibility warning explicitly.

- [ ] **Step 4: Update project memory only with durable outcomes.**

Add one `log.md` line (200 characters or fewer) for the completed validation.
Remove the GUI rollout memo item only when every listed scenario and manual
gate is complete; otherwise replace it with the next concrete uncovered
scenario.

## Plan self-review

- Coverage: launch/compatibility, navigation, all existing mutable domains,
  schedules, error recovery, focus, narrow layout, and manual visual review
  each map to a numbered task.
- Root cause: Task 1 and Task 3 cover the reproduced source-GUI versus
  installed-daemon mismatch; no task assumes the click handler is broken.
- Safety: Task 3 refuses automatic daemon replacement when active Jobs exist.
- Scope: the plan adds validation and an explicit recovery path; it does not
  invent Dashboard/Attention production screens or change domain schema.
