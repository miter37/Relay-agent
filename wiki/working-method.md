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
