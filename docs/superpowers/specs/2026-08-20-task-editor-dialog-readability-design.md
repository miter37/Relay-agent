# Task Editor Dialog Readability Design

## Problem

`TaskEditorDialog` stacks scalar fields, input definitions, Task Interface,
review settings, and Instructions directly in one vertical layout. Their
combined minimum sizes can exceed the available screen height, leaving the top
and bottom inaccessible. Several list and multi-line controls are also too
short to read comfortably.

## Goals

- Preserve the existing single-form editing flow and payload contract.
- Cap the dialog to the available screen work area.
- Make the form body vertically scrollable on short screens or with populated
  lists.
- Keep validation errors and Save/Cancel reachable outside the scroll area.
- Give Instructions, review notes, input definitions, and interface ports
  useful minimum heights without forcing a tall dialog.
- Preserve the existing asynchronous save lifecycle.

## Design

Follow the established `ProjectEditorDialog` pattern. The dialog root contains
a resizable `QScrollArea`; its child widget owns all form sections. The error
label and button box remain direct children of the dialog root below the scroll
area. Set the initial size from the current screen's available geometry, while
retaining a practical minimum width and allowing user resizing.

Keep the current section order. Instructions retains a readable minimum height;
review notes and list editors receive moderate minimum heights. List widgets
use minimum heights rather than large fixed heights so the scroll area controls
the total dialog height.

## Compatibility and error handling

No API, serialization, field name, signal, or save behavior changes. Legacy and
advanced output-contract paths remain intact. Screen geometry uses the dialog's
current screen when available and the primary screen as fallback; if no screen
is available, the existing safe default is retained.

## Verification

- Add widget tests asserting the dialog has a scroll area, the body can exceed
  the viewport, and the footer remains outside the scroll area.
- Retain the existing width and Instructions-height assertions.
- Run focused Task GUI tests, Ruff on changed Python files, and the repository
  unittest suite when the local execution window permits.
- Use a real-platform Qt smoke check where available; the repository notes that
  offscreen Qt is unsuitable for visual glyph verification.

