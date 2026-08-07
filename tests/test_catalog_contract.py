from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.models import TaskSpec
from relay.receipts import CATALOG_RECEIPT_SCHEMA_VERSION, RECEIPT_SUMMARY_KEYS
from relay.request_builder import STANDARD_JSON_SCHEMA
from relay.validation import normalize_summary, validate_json_result


class CatalogContractTests(unittest.TestCase):
    def test_summary_normalization_collapses_whitespace_and_bounds_unicode(self):
        self.assertEqual(normalize_summary("  one\n two  ", max_chars=20, field="summary"), "one two")
        value = normalize_summary("가" * 20, max_chars=10, field="summary")
        self.assertEqual(value, "가" * 9 + "…")
        self.assertIsNone(normalize_summary("  \n  ", max_chars=20, field="summary"))

    def test_task_summary_is_optional_bounded_model_data(self):
        spec = TaskSpec(name="Report", instructions="Write it", task_summary="  Write a report.  ")
        spec.validate()
        self.assertEqual(spec.task_summary, "Write a report.")

    def test_result_summary_is_optional_and_bounded(self):
        self.assertNotIn("summary", STANDARD_JSON_SCHEMA["required"])
        self.assertEqual(STANDARD_JSON_SCHEMA["properties"]["summary"]["maxLength"], 1000)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "status": "complete",
                        "answer": "answer",
                        "summary": "  result\nsummary  ",
                        "sources": [],
                        "uncertainties": [],
                        "missing_items": [],
                        "artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            result = validate_json_result(path, 1024 * 1024)
        self.assertEqual(result["summary"], "result summary")

    def test_catalog_receipt_contract_names_summary_keys(self):
        self.assertEqual(CATALOG_RECEIPT_SCHEMA_VERSION, 3)
        self.assertEqual(RECEIPT_SUMMARY_KEYS, ("task_summary", "result_summary", "failure_reason"))


if __name__ == "__main__":
    unittest.main()
