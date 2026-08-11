from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.attention.service import AttentionService
from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import JobRequest
from relay.operations.service import OperationsDashboardService


class Phase6dAttentionDashboardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.attention_service = AttentionService(self.db)
        self.operations_service = OperationsDashboardService(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_attention_inbox_aggregates_items(self):
        # Failed Job
        j1, _ = self.engine.create_job(JobRequest(task="Task 1"), queued=True)
        self.db.update_job(j1["job_id"], status="FAILED", error_code="ALL_WORKERS_FAILED")

        # Pending Approval
        self.db.create_project(
            {
                "project_id": "p-1",
                "name": "P",
                "description": None,
                "version": 1,
                "definition_json": "{}",
            }
        )
        self.db.create_project_run(
            {
                "project_run_id": "pr-1",
                "project_id": "p-1",
                "project_version": 1,
                "project_snapshot_json": "{}",
                "status": "running",
                "trigger_type": "manual",
                "submitted_via": "cli",
            }
        )
        self.db.update_project_run("pr-1", status="failed")
        self.db.create_approval(
            {
                "approval_id": "app-1",
                "project_run_id": "pr-1",
                "node_id": "n1",
                "token": "tok1",
                "status": "pending",
            }
        )

        items = self.attention_service.list_items()
        self.assertGreaterEqual(len(items), 2)
        kinds = {i["kind"] for i in items}
        self.assertIn("failed_job", kinds)
        self.assertIn("failed_project", kinds)
        self.assertIn("approval", kinds)

    def test_dashboards_compute_stats(self):
        self.db.create_routine(
            {
                "routine_id": "r-1",
                "name": "Daily R",
                "target_type": "task",
                "target_id": "T-1",
                "rule_json": "{}",
                "timezone": "Asia/Seoul",
                "enabled": 1,
                "overlap_policy": "skip",
                "missed_policy": "skip",
                "missed_grace_seconds": 43200,
                "version_policy": "latest",
                "next_run_at_utc": None,
            }
        )
        dash = self.operations_service.routine_dashboard()
        self.assertEqual(len(dash), 1)
        self.assertEqual(dash[0]["routine_id"], "r-1")
        self.assertEqual(dash[0]["total_runs"], 0)


if __name__ == "__main__":
    unittest.main()
