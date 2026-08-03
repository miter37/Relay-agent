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
        self.assertEqual(db_job.get("receipt_schema_version"), 1)

        # Verify API response
        detail = job_detail(self.engine, job["job_id"])
        self.assertEqual(detail.get("receipt_schema_version"), RECEIPT_SCHEMA_VERSION)

    def test_get_receipt_schema_version_api(self):
        ver = get_receipt_schema_version()
        self.assertEqual(ver["receipt_schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
