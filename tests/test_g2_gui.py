from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # GUI extra is installed by the GUI smoke job.
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.config import Config
from relay.gui.job_detail import JobDetailView
from relay.gui.main_window import MainWindow
from relay.gui.tasks import TaskRunDialog, TaskRunFilePickerDialog


class G2TaskRunGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_registered_task_run_adds_files_without_duplicates(self):
        view = TaskRunDialog(task={"task_id": "weather", "name": "Weather"})
        view.add_attachments(["C:/relay/result.json", "C:/relay/report.md"])
        view.add_attachments(["C:/relay/result.json"])

        self.assertEqual(
            [view.attachment_list.item(index).text() for index in range(view.attachment_list.count())],
            ["C:/relay/result.json", "C:/relay/report.md"],
        )
        self.assertEqual(view.overrides()["attachments"], ["C:/relay/result.json", "C:/relay/report.md"])

    def test_job_file_picker_returns_checked_files(self):
        dialog = TaskRunFilePickerDialog(
            [
                {"kind": "Result", "name": "result.json", "path": "C:/relay/result.json", "size": 42},
                {"kind": "Artifact", "name": "report.md", "path": "C:/relay/report.md", "size": 2048},
            ],
        )

        dialog.file_list.item(1).setCheckState(Qt.Checked)

        self.assertEqual(dialog.selected_files()[0]["path"], "C:/relay/report.md")
        self.assertEqual(dialog.windowTitle(), "Add files from Task Run")

    def test_job_input_candidates_keep_existing_unique_files_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.json"
            artifact = root / "report.md"
            result.write_text("{}", encoding="utf-8")
            artifact.write_text("# Report", encoding="utf-8")

            candidates = MainWindow._job_input_candidates(
                {"available": True, "path": str(result), "size": 2},
                {
                    "artifacts": [
                        {"final_path": str(result), "relative_path": "duplicate.json", "size": 2},
                        {"final_path": str(artifact), "relative_path": "reports/report.md", "size": 8},
                        {"final_path": str(root / "missing.txt"), "relative_path": "missing.txt"},
                    ]
                },
            )

        self.assertEqual([item["kind"] for item in candidates], ["Result", "Artifact"])
        self.assertEqual([item["name"] for item in candidates], ["result.json", "reports/report.md"])

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

        self.assertEqual(
            labels, ["Overview", "Progress", "Inputs", "Artifacts", "Logs", "Events"]
        )
        self.assertFalse(view.cancel_button.isEnabled())
        self.assertTrue(view.cancel_button.isHidden())
        self.assertTrue(view.check_button.isHidden())
        self.assertTrue(view.rerun_button.isEnabled())
        self.assertFalse(view.rerun_button.isHidden())
        self.assertFalse(hasattr(view, "copy_task_button"))
        self.assertFalse(hasattr(view, "save_as_task_button"))
        self.assertFalse(view.open_folder_button.isEnabled())
        self.assertTrue(view.open_folder_button.isHidden())
        self.assertEqual(view.title_label.text(), "Completed task")

    def test_overview_collapses_metadata_and_progress_is_attempt_by_attempt(self):
        view = JobDetailView()
        view.set_job(
            {
                "job_id": "job-2",
                "title": "Readable task",
                "status": "COMPLETED",
                "actual_worker": "codex",
                "completed_at": "2026-08-13T09:10:00+09:00",
                "created_at": "2026-08-13T09:00:00+09:00",
                "request": {"task": "Produce the result"},
                "attempts": [
                    {"attempt_id": 1, "status": "FAILED", "worker": "codex", "error": "Retryable"},
                    {"attempt_id": 2, "status": "COMPLETED", "worker": "codex"},
                ],
            }
        )

        overview_text = view._browsers["Overview"].toPlainText()
        self.assertIn("Readable task", overview_text)
        self.assertIn("Answer", overview_text)
        self.assertIn("Requested Task", overview_text)
        self.assertNotIn("Task Run ID", overview_text)
        self.assertEqual(view.overview_result_label.text(), "Result")
        self.assertTrue(view.overview_details_button.isCheckable())

        view._browsers["Overview"].selectAll()
        view.overview_details_button.click()
        self.assertIn("Task Run ID", view._browsers["Overview"].toPlainText())
        self.assertEqual(view.overview_details_button.text(), "See less")
        view.overview_details_button.click()
        self.assertNotIn("Task Run ID", view._browsers["Overview"].toPlainText())
        self.assertEqual(view.overview_details_button.text(), "See more")

        progress_text = view._browsers["Progress"].toPlainText()
        self.assertIn("Attempt 1", progress_text)
        self.assertIn("Attempt 2", progress_text)
        self.assertIn("Retryable", progress_text)

    def test_public_gui_labels_use_task_run_terminology(self):
        view = TaskRunDialog(task={"task_id": "weather", "name": "Weather"})
        self.assertEqual(view.add_from_run_button.text(), "Add from Task Run")

        detail = JobDetailView()
        self.assertEqual(detail.title_label.text(), "Task Run")

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
                "actions": {"can_cancel": True, "can_check_progress": True, "can_copy": True, "can_open_folder": True},
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

        self.assertFalse(view.cancel_button.isHidden())
        self.assertFalse(view.check_button.isHidden())
        self.assertTrue(view.rerun_button.isHidden())
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

    def test_main_window_contains_task_registration_and_job_detail_views(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            window = MainWindow(config, gui_version="0.8.0", expected_home_id="home")

            self.assertTrue(hasattr(window, "runs_view"))
            self.assertTrue(hasattr(window, "job_detail_view"))
            self.assertFalse(window.register_task_button.isEnabled())
            window.close()

    def test_compatibility_mode_disables_write_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            window = MainWindow(config, gui_version="0.8.0", expected_home_id="home")

            window._set_connection("read-only", "daemon is older")

            self.assertFalse(window.register_task_button.isEnabled())
            window.close()


if __name__ == "__main__":
    unittest.main()
