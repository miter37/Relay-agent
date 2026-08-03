from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.search.embedding import NullEmbedding
from relay.search.semantic import semantic_search


class Phase6cSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_semantic_search_falls_back_to_fts5_when_backend_null(self):
        job, _ = self.engine.create_job(
            JobRequest(task="Semiconductor HBM report", title="HBM Report", worker="codex"),
            queued=True,
        )
        self.db.update_job(job["job_id"], status="COMPLETED", result_status="complete")
        self.db.index_run(job["job_id"])

        backend = NullEmbedding()
        res = semantic_search(self.db, backend, query="HBM", kind="runs")
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("fallback", False))
        self.assertIn("warning", res)
        self.assertEqual(res["items"][0]["run_id"], job["job_id"])


if __name__ == "__main__":
    unittest.main()
