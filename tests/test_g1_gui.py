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

    def test_finished_filter_and_status_rendering(self):
        self.window.current_mode = "normal"
        self.window.jobs = {
            "done": {"job_id": "done", "status": "COMPLETED", "title": "Done", "submitted_via": "cli"},
            "failed": {"job_id": "failed", "status": "FAILED", "title": "Failed", "submitted_via": "gui"},
        }
        self.window._render_jobs()
        self.assertEqual(self.window.job_list.count(), 4)
        self.window.result_filter.setCurrentText("Failed")
        self.window._render_jobs()
        self.assertEqual(self.window.job_list.count(), 3)
        self.assertIn("× Failed", self.window.job_list.item(2).text())

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

    def test_result_response_populates_answer_and_raw_result_tabs(self):
        self.window.selected_job_id = "job-1"
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
