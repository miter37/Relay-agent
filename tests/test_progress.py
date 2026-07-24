from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from relay.process_supervisor import run_supervised
from relay.progress import diagnose_progress


class ProgressDiagnosisTests(unittest.TestCase):
    def _attempt(self, root: Path, *, stdout: str = "", stderr: str = "") -> dict:
        stdout_path = root / "stdout.log"
        stderr_path = root / "stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return {"stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}

    def test_running_process_with_activity_is_reported_active(self):
        with tempfile.TemporaryDirectory() as directory:
            attempt = self._attempt(Path(directory), stdout="working\n")
            result = diagnose_progress(
                {"job_id": "job-1", "status": "RUNNING", "requested_worker": "codex"},
                {
                    "process_alive": True,
                    "elapsed_seconds": 45,
                    "idle_seconds": 3,
                    "activity_observed": True,
                    "last_activity_kind": "stdout",
                    "stdout_bytes": 8,
                    "stderr_bytes": 0,
                    "workspace_files": 4,
                    "soft_stall_seconds": 120,
                },
                [attempt],
            )

        self.assertEqual(result["level"], "ok")
        self.assertEqual(result["headline"], "Agent is active")
        self.assertEqual(result["recent_activity"]["recent_line"], "working")

    def test_permission_marker_is_attention_but_generic_stderr_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocked = diagnose_progress(
                {"job_id": "job-1", "status": "RUNNING"},
                {"process_alive": True, "idle_seconds": 2, "soft_stall_seconds": 120},
                [self._attempt(root, stderr="Access denied by sandbox\n")],
            )
            normal = diagnose_progress(
                {"job_id": "job-2", "status": "RUNNING"},
                {"process_alive": True, "idle_seconds": 2, "soft_stall_seconds": 120},
                [self._attempt(root, stderr="Loading model metadata\n")],
            )

        self.assertEqual(blocked["detected_issue"]["code"], "PERMISSION_BLOCKED")
        self.assertEqual(blocked["level"], "attention")
        self.assertIsNone(normal["detected_issue"])
        self.assertEqual(normal["level"], "waiting")

    def test_soft_stall_and_relay_post_processing_are_distinguished(self):
        stalled = diagnose_progress(
            {"job_id": "job-1", "status": "RUNNING"},
            {"process_alive": True, "idle_seconds": 130, "soft_stall_seconds": 120},
            [],
        )
        validating = diagnose_progress(
            {"job_id": "job-2", "status": "VALIDATING"},
            {"process_alive": False, "idle_seconds": 1},
            [],
        )

        self.assertEqual(stalled["headline"], "No recent activity")
        self.assertEqual(validating["headline"], "Checking the result")

    def test_recent_line_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            result = diagnose_progress(
                {"job_id": "job-1", "status": "RUNNING"},
                {"process_alive": True, "idle_seconds": 1},
                [self._attempt(Path(directory), stdout="token=super-secret\n")],
            )

        self.assertEqual(result["recent_activity"]["recent_line"], "token=<redacted>")

    def test_supervisor_reports_live_and_finished_snapshots_without_extra_log_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = []
            stdout = root / "stdout.log"
            stderr = root / "stderr.log"

            run_supervised(
                command=[
                    sys.executable,
                    "-c",
                    "import time; print('working', flush=True); time.sleep(0.2)",
                ],
                cwd=root,
                stdin_bytes=None,
                env_extra={},
                stdout_path=stdout,
                stderr_path=stderr,
                timeout_seconds=5,
                soft_stall_seconds=2,
                hard_stall_seconds=4,
                poll_seconds=0.05,
                progress_callback=snapshots.append,
            )
            stdout_text = stdout.read_text(encoding="utf-8")

        self.assertTrue(snapshots[0]["process_alive"])
        self.assertFalse(snapshots[-1]["process_alive"])
        self.assertEqual(stdout_text, "working\n")


if __name__ == "__main__":
    unittest.main()
