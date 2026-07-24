# AGENTS.md

## Project Facts

- Python 3.11+ cross-platform broker with CLI, authenticated daemon API, SQLite history, Schedules, and an optional PySide6 GUI.
- Runtime data lives under Relay Home; release artifacts are `relay.pyz` and `SHA256SUMS.txt`.
- Run through the installed `relay` command or `python -m relay`; build with `python build_release.py`.
- Verify with Ruff and `python -m unittest discover -s tests`; CI covers Windows, Ubuntu, and macOS on Python 3.11–3.13.
- Current truth comes from code/tests first, then `README.md`, `wiki/`, and `docs/Relay_GUI_Development_Plan_v1.3.md`.

## Project Rules

- Preserve Relay Home data and migration compatibility; never replace or delete user databases, manifests, Jobs, or outputs casually.
- Custom Agent Apps execute argv lists without shell parsing and require a current matching deep audit before enablement.
- Keep daemon and GUI compatibility fields in lockstep; the current G5 contract is API schema revision 5 with minimum GUI 1.1.0.
- Scheduled executions remain ordinary Jobs linked to Schedule runs; deleting a Schedule must preserve prior Jobs and outputs.
- Treat built-in/legacy Agent environment behavior as compatibility-sensitive; custom manifest Agents use explicit environment filtering.
- Keep changes surgical and add regression tests for every corrected lifecycle, security, migration, or cross-platform bug.
- Do not commit generated `relay.pyz`, local Relay Home data, credentials, or secret environment values.

## Purpose

Keep a small, current memory so any agent can continue work without rebuilding context. Prefer useful knowledge over complete documentation. Use judgment.

## Instruction Priority

1. The user's instruction for this task
2. This file
3. Authoritative evidence and accepted deliverables
4. `README.md` and `wiki/`
5. `memo.md`
6. `log.md`

Never silently contradict an established fact, constraint, or decision. Investigate conflicts first.

## Structure

```text
project/
├─ AGENTS.md
├─ CLAUDE.md      # contains only: @AGENTS.md
├─ README.md      # entry point, basic usage, links to wiki/index.md
├─ log.md         # what was completed
├─ memo.md        # what still needs attention
└─ wiki/          # what is currently true
   ├─ index.md                    # routing only, one line per page
   ├─ overview.md                 # orientation: what this is, where it stands
   ├─ goals-and-scope.md          # goals, success criteria, constraints, non-goals
   ├─ project-model.md            # parts, actors, flows, dependencies, artifacts
   ├─ knowledge-and-evidence.md   # durable facts, sources, assumptions, uncertainty
   ├─ decisions.md                # consequential choices + reason, Active / Superseded
   └─ working-method.md           # workflow, tools, conventions, quality bar
```

No other wiki files or subfolders. No `TODO.md`, `PLAN.md`, second docs root, or competing project-memory file unless explicitly requested. Fit new knowledge into these seven files. Before defaulting to `overview.md`, check whether a more specific page owns it.

The wiki holds current knowledge, `memo.md` holds unresolved attention, and `log.md` records completed outcomes. Do not mix these roles.

## Before Work

Always read `memo.md` and `wiki/index.md`.

Also read `README.md` and `wiki/overview.md` when:

- continuing work from an earlier session or another agent;
- the task depends on broader project context;
- the project or task is unfamiliar;
- `memo.md` or `wiki/index.md` points to them.

Read any further wiki pages relevant to the task. Inspect the actual artifacts and authoritative evidence. Memory is a claim; authoritative evidence and current artifacts are the source of truth.

Then take the smallest safe action that satisfies the task.

## While Working

- Stay in scope.
- Do not redo what memory already settled unless new evidence justifies it.
- Mark what is fact, inference, assumption, estimate, preference, or open question.
- Preserve established terminology and constraints unless change is justified.
- Never claim something was tested, checked, booked, approved, or completed unless it was.
- Put no secrets, credentials, access keys, or sensitive personal data in project memory.
- Re-read relevant memory when scope shifts, contradictions appear, assumptions weaken, a decision may reverse, or work resumes after a long interruption.

When evidence and memory disagree, verify the evidence first, then correct the memory if needed.

## Updating Memory

Apply this test:

> Would a capable future agent make a wrong decision, repeat work, or miss a constraint if this stayed unchanged?

- **Yes:** update the fixed file that owns the knowledge.
- **No:** leave it unchanged.

Apply the same test to `Project Facts` and `Project Rules`.

Work happening is not itself a reason to update memory. Replace stale statements; do not stack corrections beneath them. Merge duplicates instead of maintaining competing versions.

## Log and Memo

### `log.md`

Newest first, one line per meaningful completed change, no more than 200 characters:

```md
- YYYY-MM-DD HH:MM | Outcome
```

Record the outcome, not the process. Add no entry when nothing meaningful changed.

### `memo.md`

```md
- [ ] Unresolved item
```

Delete an item once resolved. Durable knowledge belongs in the owning wiki page instead.

## Finishing

1. Confirm the requested result and verify it using the best available check.
2. Remove temporary or misleading artifacts.
3. Apply the memory-update test, resolve `memo.md`, and add a log entry when appropriate.
4. Report what changed, what was verified and how, what memory was updated, and what relevant memory was intentionally left unchanged.

Keep the memory report to one concise line. Use `Memory unchanged — existing memory remains accurate.` when no update was needed.

Work is not complete while project memory is known to contradict current reality.
