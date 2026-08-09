from __future__ import annotations

import unittest

from tests.fixtures.project_run_cases import project_run_case


class ProjectRunCaseFixtureTests(unittest.TestCase):
    def test_success_parallel_case_captures_l1_success_shape(self):
        case = project_run_case("success_parallel")
        self.assertEqual(case["catalog_item"]["status"], "completed")
        self.assertEqual(len(case["snapshot"]["project_definition"]["nodes"]), 6)
        self.assertEqual(
            {item["role"] for item in case["artifacts"]},
            {"report_json", "final_report", "assets_bundle"},
        )
        self.assertEqual(case["steps"][0]["node_id"], "official_research")
        self.assertEqual(case["steps"][1]["node_id"], "media_research")

    def test_schema_failure_case_captures_failed_and_blocked_nodes(self):
        case = project_run_case("schema_failure_blocked")
        self.assertEqual(case["catalog_item"]["failed_node_id"], "image_collection")
        self.assertEqual(case["catalog_item"]["blocked_step_count"], 1)
        statuses = {step["node_id"]: step["status"] for step in case["steps"]}
        self.assertEqual(statuses["image_collection"], "failed")
        self.assertEqual(statuses["render_and_qa"], "blocked")

    def test_dispatch_failure_case_preserves_unsupported_worker_reason(self):
        case = project_run_case("unsupported_worker_blocked")
        self.assertEqual(case["catalog_item"]["error_code"], "UNSUPPORTED_WORKER")
        self.assertEqual(case["catalog_item"]["blocked_step_count"], 4)
        self.assertEqual(case["task_run_details"]["media_research"]["error_code"], "UNSUPPORTED_WORKER")

    def test_cancelled_and_retry_cases_are_distinct(self):
        cancelled = project_run_case("cancelled")
        retry = project_run_case("retry_then_success")
        self.assertEqual(cancelled["catalog_item"]["status"], "cancelled")
        self.assertIsNone(cancelled["catalog_item"]["failed_node_id"])
        self.assertEqual(len(retry["receipt"]["steps"][0]["task_runs"]), 2)
        self.assertEqual(retry["receipt"]["steps"][0]["task_runs"][0]["status"], "failed")
        self.assertEqual(retry["receipt"]["steps"][0]["task_runs"][1]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
