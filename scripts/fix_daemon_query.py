import pathlib

p = pathlib.Path("relay/daemon.py")
t = p.read_text(encoding="utf-8")

# 1. GET /v1/tasks -> parse name + limit
old_tasks = (
    '        if path == "/v1/tasks":\n'
    "            self._json(HTTPStatus.OK, list_tasks(self.daemon.engine))\n"
    "            return\n"
)
new_tasks = (
    '        if path == "/v1/tasks":\n'
    "            try:\n"
    '                name = (params.get("name") or [None])[0]\n'
    '                limit = int((params.get("limit") or ["200"])[0])\n'
    "            except ValueError:\n"
    '                self._api_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "limit must be an integer.")\n'
    "                return\n"
    "            self._json(HTTPStatus.OK, list_tasks(self.daemon.engine, name=name, limit=limit))\n"
    "            return\n"
)
if old_tasks in t and 'name = (params.get("name")' not in t:
    t = t.replace(old_tasks, new_tasks, 1)
    print("updated /v1/tasks")
else:
    print("skipped /v1/tasks")

# 2. GET /v1/projects -> parse name + limit
old_projects = (
    '        if path == "/v1/projects":\n'
    "            self._json(HTTPStatus.OK, list_projects(self.daemon.engine))\n"
    "            return\n"
)
new_projects = (
    '        if path == "/v1/projects":\n'
    "            try:\n"
    '                name = (params.get("name") or [None])[0]\n'
    '                limit = int((params.get("limit") or ["200"])[0])\n'
    "            except ValueError:\n"
    '                self._api_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "limit must be an integer.")\n'
    "                return\n"
    "            self._json(HTTPStatus.OK, list_projects(self.daemon.engine, name=name, limit=limit))\n"
    "            return\n"
)
if old_projects in t and 'name = (params.get("name")' not in t:
    t = t.replace(old_projects, new_projects, 1)
    print("updated /v1/projects")
else:
    print("skipped /v1/projects")

# 3. GET /v1/projects/{id}/runs -> parse limit
old_pj_runs = (
    '        if path.startswith("/v1/projects/"):\n'
    '            suffix = path[len("/v1/projects/") :]\n'
    "            try:\n"
    '                if suffix.endswith("/runs"):\n'
    '                    pid = suffix[: -len("/runs")]\n'
    "                    self._json(HTTPStatus.OK, project_runs(self.daemon.engine, pid))\n"
    "                else:\n"
    "                    self._json(HTTPStatus.OK, get_project(self.daemon.engine, suffix))\n"
)
new_pj_runs = (
    '        if path.startswith("/v1/projects/"):\n'
    '            suffix = path[len("/v1/projects/") :]\n'
    "            try:\n"
    '                if suffix.endswith("/runs"):\n'
    '                    pid = suffix[: -len("/runs")]\n'
    "                    try:\n"
    '                        limit = int((params.get("limit") or ["50"])[0])\n'
    "                    except ValueError:\n"
    '                        self._api_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "limit must be an integer.")\n'
    "                        return\n"
    "                    self._json(HTTPStatus.OK, project_runs(self.daemon.engine, pid, limit=limit))\n"
    "                else:\n"
    "                    self._json(HTTPStatus.OK, get_project(self.daemon.engine, suffix))\n"
)
if old_pj_runs in t and "project_runs(self.daemon.engine, pid, limit" not in t:
    t = t.replace(old_pj_runs, new_pj_runs, 1)
    print("updated /v1/projects/{id}/runs")
else:
    print("skipped project runs route")

p.write_text(t, encoding="utf-8")
print("done")
