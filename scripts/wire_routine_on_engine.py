import pathlib

p = pathlib.Path("relay/daemon.py")
t = p.read_text(encoding="utf-8")
marker = "        self.routine_runtime = RoutineRuntime(self.config, self.db, self.engine, self.routine_service)"
insertion_line = "        self.engine.routine_service = self.routine_service"
if marker in t and insertion_line not in t:
    t = t.replace(marker, marker + "\n" + insertion_line, 1)
    p.write_text(t, encoding="utf-8")
    print("wired")
else:
    print("skipped")
