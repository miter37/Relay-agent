import pathlib
p = pathlib.Path("relay/daemon.py")
t = p.read_text(encoding="utf-8")
old = (
    "            routines = self.daemon.engine.routine_service.list_routines(name=name, limit=limit)\n"
    '            self._json(HTTPStatus.OK, {"ok": True, "routines": [_routine_public(r) for r in routines]})\n'
)
new = (
    "            routines = self.daemon.routine_service.list_routines(name=name, limit=limit)\n"
    '            self._json(HTTPStatus.OK, {"ok": True, "routines": [_routine_public(r) for r in routines]})\n'
)
if old in t:
    t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print("updated")
else:
    print("not found")
