from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - GUI extra is optional
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.reviews import ReviewsView


class ReviewsArtifactExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_review_renders_candidate_artifacts_in_shared_explorer(self):
        view = ReviewsView()
        view.set_review(
            {
                "review": {"review_id": "review-1", "status": "pending_human", "scope_type": "task"},
                "current_round": {"round_no": 1, "task_run_id": "job-1"},
                "artifacts": [
                    {
                        "artifact_uid": "candidate-notes",
                        "relative_path": "notes.md",
                        "final_path": "/tmp/notes.md",
                        "mime_type": "text/markdown",
                        "publication_status": "candidate",
                    }
                ],
                "candidate_result": {
                    "path": "/tmp/result.json",
                    "text": '{"answer": "candidate"}',
                },
            }
        )

        self.assertEqual(view.artifacts_view._selected_artifact_uid, "candidate-notes")
        self.assertEqual(view.artifacts_view.selected_record().review_id, "review-1")
        self.assertEqual(view.artifacts_view.preview_stack.currentWidget(), view.artifacts_view.metadata_preview)
        self.assertIn("Loading", view.artifacts_view.metadata_preview.text())


if __name__ == "__main__":
    unittest.main()
