import pathlib
p = pathlib.Path("relay/daemon.py")
t = p.read_text(encoding="utf-8")
old = (
    '        if path == "/v1/routines":\n'
    "            self._json(HTTPStatus.OK, list_routines(self.daemon.engine))\n"
    "            return\n"
)
new = (
    '        if path == "/v1/routines":\n'
    "            try:\n"
    '                name = (params.get("name") or [None])[0]\n'
    '                limit = int((params.get("limit") or ["200"])[0])\n'
    "            except ValueError:\n"
    '                self._api_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "limit must be an integer.")\n'
    "                return\n"
    "            routines = self.daemon.engine.routine_service.list_routines(name=name, limit=limit)\n"
    '            self._json(HTTPStatus.OK, {"ok": True, "routines": [_routine_public(r) for r in routines]})\n'
    "            return\n"
)
if old in t and 'name = (params.get("name")' not in t.split('        if path == "/v1/routines":')[1].split('        if path.startswith("/v1/projects/":')[0]:
    t = t.replace(old, new, 1)
    print("updated /v1/routines GET")
else:
    print("skipped /v1/routines GET")

# /v1/routines/{id}/runs -> parse limit
old_runs = (
    "                if suffix.endswith(\"/runs\"):\n"
    "                    rid = suffix[: -len(\"/runs\")]\n"
    "                    self._json(HTTPStatus.OK, routine_runs(self.daemon.engine, rid))\n"
)
new_runs = (
    "                if suffix.endswith(\"/runs\"):\n"
    "                    rid = suffix[: -len(\"/runs\")]\n"
    "                    try:\n"
    '                        limit = int((params.get("limit") or ["100"])[0])\n'
    "                    except ValueError:\n"
    '                        self._api_error(HTTPStatus.BAD_REQUEST, "INVALID_REQUEST", "limit must be an integer.")\n'
    "                        return\n"
    "                    rows = self.daemon.engine.db.list_routine_runs(routine_id=rid, limit=limit)\n"
    '                    self._json(HTTPStatus.OK, {"ok": True, "routine_id": rid, "runs": rows})\n'
)
if old_runs in t and 'list_routine_runs' not in t:
    t = t.replace(old_runs, new_runs, 1)
    print("updated /v1/routines/{id}/runs")
else:
    print("skipped /v1/routines/{id}/runs")

p.write_text(t, encoding="utf-8")
print("done")
