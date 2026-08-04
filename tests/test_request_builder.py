from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.models import JobRequest
from relay.request_builder import build_request_markdown


class RequestBuilderTests(unittest.TestCase):
    def test_artifact_input_points_to_worker_workspace_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = JobRequest(task="Use A1", result_format="json")
            text = build_request_markdown(
                request,
                root / "output.json",
                root / "artifacts",
                [],
                artifact_inputs=[
                    {
                        "alias": "A1",
                        "snapshot_relative_path": "input_snapshots/run/A1-source.md",
                        "source_job_id": "source-run",
                        "source_relative_path": "source.md",
                        "snapshot_sha256": "digest",
                    }
                ],
            )

        self.assertIn("`A1` at `input/A1-source.md`", text)
        self.assertNotIn("input_snapshots/run/A1-source.md", text)


if __name__ == "__main__":
    unittest.main()
