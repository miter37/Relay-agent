from __future__ import annotations

import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # GUI extra is installed by the GUI smoke job.
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay import __version__
from relay.compatibility import evaluate_compatibility, relay_home_id
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.engine import RelayEngine
from relay.gui.main_window import MainWindow
from relay.models import JobRequest
from relay.rpc import RPCClient


class G1GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temp.name) / "relay-home")
        self.config.init()
        self.window = MainWindow(self.config, gui_version="0.8.0", expected_home_id="home")

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def test_gui_has_read_only_sections_and_home_state(self):
        self.assertEqual(self.window.windowTitle(), "Relay-agent")
        self.assertEqual(self.window.sidebar.minimumWidth(), 0)
        self.assertIn("Relay Home:", self.window.statusBar().currentMessage())
        self.assertFalse(hasattr(self.window, "health_timer"))
        self.assertIn("Health:", self.window.health_label.text())

    def test_health_status_is_visible_and_uses_manual_refresh(self):
        self.window._set_connection(
            "normal",
            health={
                "daemon_version": "1.1.0",
                "api_schema_revision": 5,
                "started_at": "2026-07-24T01:00:00+00:00",
            },
        )

        self.assertEqual(self.window.health_label.text(), "Health: Healthy")
        self.assertIn("Daemon 1.1.0", self.window.health_label.toolTip())
        self.assertIn("API schema 5", self.window.health_label.toolTip())

    def test_unhealthy_enabled_engines_are_named_instead_of_marked_healthy(self):
        self.window._set_connection(
            "normal",
            health={
                "worker_health": {
                    "status": "unhealthy",
                    "unhealthy": [{"agent_id": "claude", "code": "WORKER_UNVERIFIED"}],
                }
            },
        )

        self.assertEqual(self.window.health_label.text(), "Unhealthy: claude")
        self.assertIn("claude: WORKER_UNVERIFIED", self.window.health_label.toolTip())

    def test_finished_filter_and_status_rendering(self):
        self.window.current_mode = "normal"
        self.window.jobs = {
            "done": {"job_id": "done", "status": "COMPLETED", "title": "Done", "submitted_via": "cli"},
            "failed": {"job_id": "failed", "status": "FAILED", "title": "Failed", "submitted_via": "gui"},
        }
        self.window._render_jobs()
        self.assertEqual(self.window.job_list.topLevelItemCount(), 1)
        self.window.result_filter.setCurrentText("Failed")
        self.window._render_jobs()
        self.assertEqual(self.window.job_list.topLevelItemCount(), 1)
        failed_group = self.window.job_list.topLevelItem(0)
        failed_date = failed_group.child(0)
        failed_task = failed_date.child(0)
        self.assertEqual(failed_task.text(0), "Failed")
        self.assertEqual(failed_task.text(1), "Fail")

    def test_active_refresh_uses_one_request_and_refreshes_selected_detail(self):
        self.window.current_mode = "normal"
        self.window.selected_job_id = "job-1"
        requests = []
        self.window._request = lambda kind, path: requests.append((kind, path))

        self.window._refresh_active()

        self.assertEqual(requests, [("active", "/v1/jobs?bucket=active&limit=200")])
        self.window.pending[1] = "active"
        self.window._handle_response(
            1,
            {"jobs": [{"job_id": "job-1", "status": "RUNNING", "title": "Current job"}]},
            None,
        )
        self.assertEqual(self.window.jobs["job-1"]["status"], "RUNNING")
        self.assertEqual(requests[-1], (("detail", "job-1"), "/v1/jobs/job-1"))

    def test_stale_detail_response_does_not_replace_current_selection(self):
        self.window.selected_job_id = "job-2"
        self.window.pending[1] = ("detail", "job-1")

        self.window._handle_response(1, {"job_id": "job-1", "status": "COMPLETED"}, None)

        self.assertIsNone(self.window.current_detail)

    def test_new_task_ignores_stale_detail_response(self):
        self.window.selected_job_id = "job-1"
        self.window.detail_view_mode = "job"
        self.window._show_new_task()
        self.window.pending[1] = ("detail", "job-1")

        self.window._handle_response(1, {"job_id": "job-1", "status": "COMPLETED"}, None)

        self.assertIs(self.window.detail_stack.currentWidget(), self.window.new_task_view)

    def test_settings_ignores_stale_detail_response(self):
        self.window.selected_job_id = "job-1"
        self.window.detail_view_mode = "job"
        self.window._show_settings()
        self.window.pending[1] = ("detail", "job-1")

        self.window._handle_response(1, {"job_id": "job-1", "status": "COMPLETED"}, None)

        self.assertIs(self.window.detail_stack.currentWidget(), self.window.settings_view)

    def test_finished_tree_can_collapse_group_and_date(self):
        self.window.jobs = {
            "done": {
                "job_id": "done",
                "status": "COMPLETED",
                "title": "Done",
                "completed_at": "2026-07-24T01:00:00+00:00",
            }
        }
        self.window._render_jobs()
        group = self.window.job_list.topLevelItem(0)
        date = group.child(0)
        group.setExpanded(False)
        date.setExpanded(False)

        self.window._render_jobs()

        self.assertFalse(self.window.job_list.topLevelItem(0).isExpanded())
        self.assertFalse(self.window.job_list.topLevelItem(0).child(0).isExpanded())

    def test_result_response_populates_answer_and_raw_result_tabs(self):
        self.window.selected_job_id = "job-1"
        self.window.detail_view_mode = "job"
        self.window.pending[1] = ("result", "job-1")
        payload = {
            "job_id": "job-1",
            "available": True,
            "data": {"answer": "## Summary\n\nReadable answer"},
        }

        self.window._handle_response(1, payload, None)

        self.assertIn("Readable answer", self.window.job_detail_view.answer_browser.toPlainText())
        self.assertIn("available", self.window.job_detail_view._browsers["Result"].toPlainText())

    def test_unsupported_schema_is_read_only(self):
        health = {"ok": True, "api_versions": ["v1"], "api_schema_revision": 99, "min_gui_version": "0.8.0"}
        self.assertEqual(evaluate_compatibility(health, gui_version=__version__).mode, "read-only")

    def test_gui_connects_to_real_daemon_and_loads_jobs(self):
        self.window.close()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        self.config.set("daemon_port", port)
        db = Database(self.config.path_value("database_path"))
        engine = RelayEngine(self.config, db)
        job, _ = engine.create_job(
            JobRequest(task="GUI integration smoke", worker="codex"),
            queued=True,
            submitted_via="cli",
        )
        db.update_job(job["job_id"], status="COMPLETED", completed_at="2026-07-24T00:00:00+00:00")
        daemon = RelayDaemon(self.config)
        daemon.scheduler.stop_event.set()
        daemon.schedule_loop.stop_event.set()
        thread = threading.Thread(target=daemon.serve, daemon=True)
        thread.start()
        client = RPCClient(self.config)
        self.assertTrue(client.wait_until_healthy(3))
        try:
            self.window = MainWindow(
                self.config,
                gui_version=__version__,
                expected_home_id=relay_home_id(self.config.home),
            )
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(
                lambda: (
                    loop.quit() if self.window.current_mode == "normal" and job["job_id"] in self.window.jobs else None
                )
            )
            poll.start(25)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            poll.stop()

            self.assertEqual(self.window.current_mode, "normal")
            self.assertIn(job["job_id"], self.window.jobs)
            self.assertTrue(self.window.new_task_button.isEnabled())
        finally:
            if thread.is_alive():
                client.request("POST", "/shutdown")
                thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
