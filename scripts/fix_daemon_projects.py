import pathlib

p = pathlib.Path("relay/daemon.py")
t = p.read_text(encoding="utf-8")
old = (
    '        if path == "/v1/projects":\n'
    "            self._json(HTTPStatus.OK, list_projects(self.daemon.engine))\n"
    "            return\n"
)
new = (
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
if old in t and 'name = (params.get("name")' not in t:
    t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print("updated /v1/projects GET")
else:
    print("skipped /v1/projects GET")
