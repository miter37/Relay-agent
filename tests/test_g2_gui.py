from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # GUI extra is installed by the GUI smoke job.
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.config import Config
from relay.gui.job_detail import JobDetailView
from relay.gui.main_window import MainWindow
from relay.gui.new_task import NewTaskView


class G2NewTaskGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_task_payload_preserves_cli_equivalent_options(self):
        view = NewTaskView()
        view.title_edit.setText("G2 title")
        view.task_edit.setPlainText("Research the G2 API")
        view.worker_combo.setCurrentText("codex")
        view.model_edit.setText("gpt-test")
        view.profile_combo.setCurrentText("analysis-only")
        view.fallback_check.setChecked(True)
        view.timeout_spin.setValue(90)
        view.format_combo.setCurrentText("txt")
        view.output_edit.setText("/tmp/result.txt")
        view.artifact_edit.setText("/tmp/artifacts")
        view.force_new_check.setChecked(True)
        view.overwrite_check.setChecked(True)

        payload = view.payload()

        self.assertEqual(payload["title"], "G2 title")
        self.assertEqual(payload["task"], "Research the G2 API")
        self.assertEqual(payload["worker"], "codex")
        self.assertEqual(payload["model"], "gpt-test")
        self.assertEqual(payload["profile"], "analysis-only")
        self.assertTrue(payload["fallback"])
        self.assertEqual(payload["timeout_seconds"], 90)
        self.assertEqual(payload["result_format"], "txt")
        self.assertTrue(payload["force_new"])
        self.assertTrue(payload["overwrite"])

    def test_new_task_defaults_enable_fallback_and_advanced_execution_options(self):
        view = NewTaskView()

        payload = view.payload()

        self.assertTrue(payload["fallback"])
        self.assertTrue(payload["force_new"])
        self.assertTrue(payload["overwrite"])
        self.assertTrue(view.advanced_toggle.isChecked())

    def test_job_detail_has_g2_tabs_and_replay_gating(self):
        view = JobDetailView()
        view.set_job(
            {
                "job_id": "job-1",
                "title": "Completed task",
                "status": "COMPLETED",
                "actions": {"can_cancel": False, "can_rerun": True, "can_copy": True},
            }
        )

        labels = [view.tabs.tabText(index) for index in range(view.tabs.count())]

        self.assertEqual(labels, ["Overview", "Task", "Progress", "Answer", "Result", "Files", "Logs", "Events"])
        self.assertFalse(view.cancel_button.isEnabled())
        self.assertTrue(view.rerun_button.isEnabled())
        self.assertFalse(view.copy_task_button.isEnabled())
        self.assertFalse(view.open_folder_button.isEnabled())

    def test_answer_tab_renders_markdown_and_copies_plain_text(self):
        view = JobDetailView()

        view.set_answer("## Summary\n\n- First point\n- Second point")
        view.copy_answer_button.click()

        self.assertIn("Summary", view.answer_browser.toPlainText())
        self.assertIn("First point", view.answer_browser.toPlainText())
        self.assertEqual(QApplication.clipboard().text(), "## Summary\n\n- First point\n- Second point")
        self.assertTrue(view.copy_answer_button.isEnabled())

    def test_detail_refresh_preserves_answer_until_job_changes(self):
        view = JobDetailView()
        view.set_job({"job_id": "job-1", "status": "RUNNING"})
        view.set_answer("Current answer")

        view.set_job({"job_id": "job-1", "status": "COMPLETED"})
        self.assertEqual(view.answer_text, "Current answer")

        view.set_job({"job_id": "job-2", "status": "QUEUED"})
        self.assertEqual(view.answer_text, "")
        self.assertFalse(view.copy_answer_button.isEnabled())

    def test_job_detail_exposes_log_controls_and_open_actions(self):
        view = JobDetailView()
        view.set_job(
            {
                "job_id": "job-2",
                "status": "RUNNING",
                "actions": {"can_cancel": True, "can_copy": True, "can_open_folder": True},
                "output_path": "/tmp/result.json",
                "artifact_path": "/tmp/artifacts",
                "request": {"task": "Copy this task"},
                "attempts": [
                    {
                        "attempt_id": 7,
                        "worker": "codex",
                        "stdout_path": "/tmp/stdout.log",
                        "stderr_path": "/tmp/stderr.log",
                    }
                ],
            }
        )

        self.assertTrue(view.copy_task_button.isEnabled())
        self.assertTrue(view.open_folder_button.isEnabled())
        self.assertEqual(view.attempt_combo.currentData(), 7)
        self.assertEqual(view.stream_combo.currentText(), "stdout")
        self.assertTrue(view.open_log_button.isEnabled())
        view.set_content("Logs", "<pre>ERROR failed</pre>")

    def test_running_job_exposes_check_button_and_separate_check_stream(self):
        view = JobDetailView()
        checked = []
        view.check_requested.connect(checked.append)
        view.set_job(
            {
                "job_id": "job-check",
                "status": "RUNNING",
                "actions": {"can_check_progress": True},
                "attempts": [{"attempt_id": 4, "worker": "codex"}],
            }
        )

        view.check_button.click()
        view.select_check_results()

        self.assertEqual(checked, ["job-check"])
        self.assertEqual(view.tabs.tabText(view.tabs.currentIndex()), "Logs")
        self.assertTrue(view.is_check_stream())
        self.assertFalse(view.attempt_combo.isEnabled())
        self.assertFalse(view.open_log_button.isEnabled())

    def test_main_window_contains_new_task_and_job_detail_views(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            window = MainWindow(config, gui_version="0.8.0", expected_home_id="home")

            self.assertTrue(hasattr(window, "new_task_view"))
            self.assertTrue(hasattr(window, "job_detail_view"))
            self.assertFalse(window.new_task_button.isEnabled())
            window.close()

    def test_compatibility_mode_disables_write_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            window = MainWindow(config, gui_version="0.8.0", expected_home_id="home")

            window._set_connection("read-only", "daemon is older")

            self.assertFalse(window.new_task_button.isEnabled())
            self.assertFalse(window.new_task_view.create_button.isEnabled())
            window.close()


if __name__ == "__main__":
    unittest.main()
