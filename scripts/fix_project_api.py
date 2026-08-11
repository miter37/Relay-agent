import pathlib

p = pathlib.Path("relay/api.py")
t = p.read_text(encoding="utf-8")
old = (
    "def list_projects(engine, *, name: str | None = None, limit: int = 200, include_deleted: bool = False) -> dict[str, Any]:\n"
    '    return {"ok": True, "projects": [_project_public(p) for p in engine.project_service.list_projects(name=name, include_deleted=include_deleted, limit=limit)]}\n'
)
new = (
    "def list_projects(engine, *, name: str | None = None, limit: int = 200) -> dict[str, Any]:\n"
    '    return {"ok": True, "projects": [_project_public(p) for p in engine.project_service.list_projects(name=name, limit=limit)]}\n'
)
if old in t:
    t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print("updated")
else:
    print("no match")
