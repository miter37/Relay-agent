# Phase 5 Routines GUI Implementation Plan

**Goal:** Add a Routines section to the GUI that lists, shows, creates, edits,
deletes, and triggers run-now for registered Routines, without depending on a
running daemon for widget tests.

**Strategy to avoid getting blocked (lessons from previous attempt):**

1. **GUI tests use a stub RPC client.** Real daemon traffic is verified only by
   one or two daemon-route regression tests. We do not test the GUI by
   spinning up a daemon.
2. **No `run-now` invocation from tests.** That code path triggered the prior
   daemon crash; we cover it by signal assertions only.
3. **Rule editing is JSON for v1.** No rule picker UI - the user pastes JSON or
   uses the existing CLI. The model already validates via `validate_rule`.
4. **Target combobox starts empty.** We do not eagerly fetch all Tasks and
   Projects at editor construction; we accept that loading them is a
   follow-up.
5. **No `target.lookup` round trips in tests.** The widget emits a payload
   containing the chosen target_id; the server validates.

---

## Files

- `relay/gui/routines.py` (new) - RoutinesListView, RoutineDetailView,
  RoutineEditorDialog, RoutinesView.
- `relay/gui/main_window.py` - add Routines button, slot, handlers.
- `tests/test_phase5_gui.py` (new) - widget and routing tests with stubbed
  RPC client. No daemon.

## Widget shapes

### RoutinesListView
- Header: title, Refresh button, New Routine button.
- Filter: search by name.
- List: `name - target_type/target_id - vN - <enabled/disabled>`.
- Counter label reusing the same pattern as Tasks/Projects.

### RoutineDetailView
- Header: title, status label (enabled/disabled badge), Refresh, Run now,
  Edit, Delete.
- Tabs:
  - Overview (id, name, target, version_policy, overlap_policy,
    missed_policy, missed_grace_seconds, timezone, active range,
    last/next run, enabled).
  - Definition (JSON view of the payload).
  - Runs (table of run_id, status, trigger, when).

### RoutineEditorDialog
- Form fields: name, target_type combo, target_id combo, rule JSON
  (multiline), timezone, enabled checkbox, overlap_policy combo,
  missed_policy combo, missed_grace_seconds, version_policy combo,
  pinned_version, input_policy JSON, notification_policy JSON,
  starts_at_utc, ends_at_utc.
- Save validates rule JSON parses; raises if not.

### RoutinesView (composite)
- Layout: list on the left, detail on the right.
- Signals: refresh, create, select, edit, delete, run, create_submitted,
  edit_submitted, run_submitted.

## Completion gate

- `python -m unittest tests.test_phase5_gui` green.
- `ruff format --check` and `ruff check` clean.
- `py_compile` clean.
