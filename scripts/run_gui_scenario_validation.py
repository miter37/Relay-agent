from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
MOCK_CODEX = ROOT / "mocks" / ("codex.cmd" if os.name == "nt" else "codex")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from relay import __version__  # noqa: E402
from relay.compatibility import relay_home_id  # noqa: E402
from relay.config import Config  # noqa: E402
from relay.daemon import RelayDaemon  # noqa: E402
from relay.db import Database  # noqa: E402
from relay.doctor import Doctor  # noqa: E402
from relay.engine import RelayEngine  # noqa: E402
from relay.gui.main_window import MainWindow  # noqa: E402
from relay.models import JobRequest, TaskSpec  # noqa: E402
from relay.rpc import RPCClient  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def pump_and_wait(predicate, timeout_ms: int = 6000) -> bool:
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start(25)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    timer.stop()
    return bool(predicate())


def main() -> int:
    results: dict = {"scenarios": [], "failures": []}
    _app = QApplication.instance() or QApplication([])

    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(ROOT / "mocks") + os.pathsep + old_path
    os.environ["RELAY_TEST_PYTHON"] = sys.executable
    os.environ["RELAY_MISSION_E2E"] = "1"

    with tempfile.TemporaryDirectory(prefix="relay-gui-val-") as temp:
        home = Path(temp) / "relay-home"
        config = Config(home)
        config.init()
        config.set("service_isolation_acknowledged", True)
        config.set("daemon_port", free_port())
        config.set("workers.codex.command", str(MOCK_CODEX))
        config.set("soft_stall_seconds", 2)
        config.set("hard_stall_seconds", 5)
        config.set("timeout_seconds", 30)
        config.set("poll_interval_seconds", 0.2)
        config.set("workers.codex.enabled", True)
        config.set("workers.codex.security_verified", True)

        db = Database(config.path_value("database_path"))
        engine = RelayEngine(config, db)

        seeded_task = engine.create_task(
            TaskSpec(
                name="GUI validation task",
                instructions="Produce a short validation note.",
                task_summary="Produce a short validation note.",
                default_worker="codex",
            )
        )

        daemon = RelayDaemon(config)
        dthread = threading.Thread(target=daemon.serve, name="gui-val-daemon", daemon=True)
        dthread.start()
        client = RPCClient(config)
        assert client.wait_until_healthy(5), "daemon did not become healthy"

        audit = Doctor(config, db).audit(["codex"], deep=True)
        assert audit["ok"], f"codex doctor failed: {audit}"

        window = MainWindow(config, gui_version=__version__, expected_home_id=relay_home_id(config.home))
        window.show()

        def record(name: str, ok: bool, detail: str = "") -> None:
            results["scenarios"].append({"id": name, "ok": ok, "detail": detail})
            if not ok:
                results["failures"].append({"id": name, "detail": detail})

        try:
            ok = pump_and_wait(lambda: window.current_mode == "normal", timeout_ms=8000)
            record(
                "L-01",
                ok and window.new_task_button.isEnabled(),
                f"mode={window.current_mode} new_task_enabled={window.new_task_button.isEnabled()} "
                f"health={window.health_label.text()!r}",
            )

            seeded_job, _, _ = engine.run_task(
                seeded_task["task_id"],
                request=JobRequest(task="", worker="codex"),
                queued=True,
                submitted_via="cli",
            )
            for _ in range(80):
                QApplication.processEvents()
                job = db.get_job(seeded_job["job_id"])
                if job and job["status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
                    break
                time.sleep(0.2)
            final_status = db.get_job(seeded_job["job_id"])["status"]
            record(
                "R-history",
                final_status in {"COMPLETED", "PARTIAL"},
                f"seeded_run_status={final_status}",
            )

            window._show_runs()
            record(
                "R-01",
                window.detail_view_mode == "runs" and window.runs_button.isChecked(),
                f"mode={window.detail_view_mode} runs_checked={window.runs_button.isChecked()}",
            )
            pump_and_wait(lambda: bool(window.jobs), timeout_ms=4000)

            window._show_tasks()
            tasks_loaded = pump_and_wait(lambda: bool(window.tasks_index), timeout_ms=6000)
            tasks = dict(window.tasks_index)
            seeded_visible = any(t.get("task_id") == seeded_task["task_id"] for t in tasks.values())
            record(
                "T-01",
                window.detail_view_mode == "tasks" and tasks_loaded and seeded_visible,
                f"mode={window.detail_view_mode} task_count={len(tasks)} seeded_visible={seeded_visible}",
            )

            window._show_new_task()
            record(
                "R-02",
                window.detail_view_mode == "new_task" and window.detail_stack.currentWidget() is window.new_task_view,
                f"mode={window.detail_view_mode}",
            )

            window._show_projects()
            record(
                "P-nav",
                window.detail_view_mode == "projects" and window.projects_button.isChecked(),
                f"mode={window.detail_view_mode} projects_checked={window.projects_button.isChecked()}",
            )

            window._show_routines()
            record(
                "U-nav",
                window.detail_view_mode == "routines" and window.routines_button.isChecked(),
                f"mode={window.detail_view_mode} routines_checked={window.routines_button.isChecked()}",
            )

            health_text = window.health_label.text()
            health_ok = health_text == "Health: Healthy" or health_text.startswith("Unhealthy:")
            record(
                "Health",
                health_ok,
                f"health={health_text!r}",
            )

            nav_text = " ".join(
                b.text()
                for b in (
                    window.runs_button,
                    window.tasks_button,
                    window.projects_button,
                    window.routines_button,
                )
            )
            record("Terminology", "Jobs" not in nav_text, f"nav={nav_text!r}")

            # L-03: incompatible daemon -> read-only mode disables mutations and shows a banner reason.
            window._set_connection("read-only", "daemon does not support the required API")
            banner_explains = "required API" in window.banner.text()
            button_disabled = not window.new_task_button.isEnabled()
            button_explains = (
                bool(window.new_task_button.toolTip()) and "required API" in window.new_task_button.toolTip()
            )
            record(
                "L-03",
                button_disabled and banner_explains,
                f"new_task_disabled={button_disabled} banner_explains={banner_explains} "
                f"button_tooltip_explains={button_explains}",
            )
            # Return to normal for clean teardown.
            window._set_connection("normal", health={})

        finally:
            window.close()
            try:
                client.request("POST", "/shutdown")
            except Exception:
                pass
            dthread.join(timeout=5)

    os.environ["PATH"] = old_path
    os.environ.pop("RELAY_MISSION_E2E", None)

    results["passed"] = sum(1 for s in results["scenarios"] if s["ok"])
    results["total"] = len(results["scenarios"])
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if not results["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
