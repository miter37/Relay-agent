import pathlib

for path in ["relay/engine.py", "relay/daemon.py"]:
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8")
    if "self.routine_service" in t and "RoutineService(" not in t.split("self.routine_service")[0]:
        continue
    # No-op if already wired (engine.py doesn't have it; daemon does)
    if path == "relay/engine.py" and "self.routine_service" not in t:
        # Insert after the project_service line
        marker = "        self.project_service = ProjectService(self.db, self)"
        # RoutineService needs config + db + engine; engine has db but no config attr
        # Use lazy proxy: store factory then bind in daemon
        t = t.replace(
            marker,
            marker + "\n        self.routine_service = None  # wired by RelayDaemon to keep engine config-free",
            1,
        )
        if "self.routine_service = None" in t:
            p.write_text(t, encoding="utf-8")
            print(f"{path}: added routine_service placeholder")
        else:
            print(f"{path}: marker missing")
    elif path == "relay/daemon.py":
        # Make daemon assign engine.routine_service after instantiation
        marker = (
            "        self.routine_runtime = RoutineRuntime(self.config, self.db, self.engine, self.routine_service)"
        )
        if "self.engine.routine_service = self.routine_service" not in t:
            t = t.replace(
                marker,
                marker + "\n        self.engine.routine_service = self.routine_service",
                1,
            )
            p.write_text(t, encoding="utf-8")
            print(f"{path}: wired routine_service on engine")
        else:
            print(f"{path}: already wired")
