from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.api import RECEIPT_SCHEMA_VERSION, get_receipt_schema_version, job_detail
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest


class Phase6eReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_job_receipt_carries_receipt_schema_version(self):
        job, _ = self.engine.create_job(JobRequest(task="Test Receipt"), queued=True)

        # Verify DB persistence
        db_job = self.db.get_job(job["job_id"])
        self.assertEqual(db_job.get("receipt_schema_version"), 2)

        # Verify API response
        detail = job_detail(self.engine, job["job_id"])
        self.assertEqual(detail.get("receipt_schema_version"), RECEIPT_SCHEMA_VERSION)

    def test_get_receipt_schema_version_api(self):
        ver = get_receipt_schema_version()
        self.assertEqual(ver["receipt_schema_version"], 2)

    def test_failure_receipt_contains_normalized_summary_fields(self):
        job, _ = self.engine.create_job(JobRequest(task="Test failure"), queued=True)
        receipt = self.engine._fail_job(job["job_id"], "AUTH_REQUIRED", "  Agent login\nexpired.  ", [])

        self.assertEqual(receipt["receipt_schema_version"], 2)
        self.assertIn("task_summary", receipt)
        self.assertIsNone(receipt["result_summary"])
        self.assertEqual(receipt["failure_reason"], "Agent login expired.")

    def test_result_summary_prefers_agent_summary_then_falls_back(self):
        self.assertEqual(
            self.engine._resolve_result_summary({"summary": "  Actual result.  ", "answer": "Other"}, None),
            "Actual result.",
        )
        self.assertEqual(self.engine._resolve_result_summary({"answer": "Answer text"}, None), "Answer text")
        self.assertEqual(self.engine._resolve_result_summary(None, "Text result"), "Text result")


if __name__ == "__main__":
    unittest.main()
