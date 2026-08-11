import pathlib

p = pathlib.Path("relay/api.py")
t = p.read_text(encoding="utf-8")

old_tasks = (
    "def list_tasks(engine) -> dict[str, Any]:\n"
    '    return {"ok": True, "tasks": [_task_public(t) for t in engine.db.list_tasks(limit=200)]}\n'
)
new_tasks = (
    "def list_tasks(engine, *, name: str | None = None, limit: int = 200) -> dict[str, Any]:\n"
    '    return {"ok": True, "tasks": [_task_public(t) for t in engine.db.list_tasks(name=name, limit=limit)]}\n'
)
if old_tasks in t and "def list_tasks(engine, *, name" not in t:
    t = t.replace(old_tasks, new_tasks, 1)
    print("updated list_tasks")
else:
    print("skipped list_tasks")

old_projects = (
    "def list_projects(engine) -> dict[str, Any]:\n"
    '    return {"ok": True, "projects": [_project_public(p) for p in engine.project_service.list_projects(limit=200)]}\n'
)
new_projects = (
    "def list_projects(engine, *, name: str | None = None, limit: int = 200, include_deleted: bool = False) -> dict[str, Any]:\n"
    '    return {"ok": True, "projects": [_project_public(p) for p in engine.project_service.list_projects(name=name, include_deleted=include_deleted, limit=limit)]}\n'
)
if old_projects in t and "def list_projects(engine, *, name" not in t:
    t = t.replace(old_projects, new_projects, 1)
    print("updated list_projects")
else:
    print("skipped list_projects")

old_pj_runs = (
    "def project_runs(engine, project_id: str) -> dict[str, Any]:\n"
    "    rows = engine.db.list_project_runs(project_id=project_id, limit=50)\n"
)
new_pj_runs = (
    "def project_runs(engine, project_id: str, *, limit: int = 50) -> dict[str, Any]:\n"
    "    rows = engine.db.list_project_runs(project_id=project_id, limit=limit)\n"
)
if old_pj_runs in t and "def project_runs(engine, project_id: str, *, limit" not in t:
    t = t.replace(old_pj_runs, new_pj_runs, 1)
    print("updated project_runs")
else:
    print("skipped project_runs")

p.write_text(t, encoding="utf-8")
print("done")
