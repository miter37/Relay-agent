# Working method

1. Read `memo.md` and `wiki/index.md`; read the owning wiki page and current source before changing behavior.
2. Work on a feature branch and preserve unrelated or user-owned worktree changes.
3. Reproduce bugs with focused tests, make the smallest implementation change, then run the focused suite.
4. Before handoff run:

```powershell
py -m ruff format --check .
py -m ruff check .
py -m unittest discover -s tests
py build_release.py
py relay.pyz version
git diff --check
```

5. Do not wait for or poll GitHub CI by default after a push; check it only when the user asks or before a merge or release.
6. Update memory only when stale memory would cause a future agent to make a wrong decision, repeat work, or miss a constraint.
7. After changing adapter, validation, or any other runtime module, restart the daemon before judging the GUI. A running daemon holds the modules it imported at startup, so the CLI (fresh import) can pass while the GUI's daemon-side path still runs the old code.
8. When writing Task instructions, keep absolute filesystem paths out of the text. `infer_target_path` turns any path plus a write-intent word into an inferred Working folder, so one path silently becomes the target (then fails TARGET_NOT_MODIFIED) and two raise TARGET_PATH_AMBIGUOUS. Write `$HOME/...` or `$VAR/...`: the path regexes require the leading `/` to follow a non-word character, so an expanded prefix defeats them. Check with `infer_target_path(text)` before registering.
