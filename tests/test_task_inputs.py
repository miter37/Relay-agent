from __future__ import annotations

import unittest

from relay.task_inputs import apply_defaults, compile_definitions, extract_definitions, validate_inputs


class TaskInputsTests(unittest.TestCase):
    def setUp(self):
        self.schema = compile_definitions(
            [
                {"name": "City", "value_type": "text", "cardinality": "single", "required": True},
                {
                    "name": "Budget",
                    "value_type": "number",
                    "cardinality": "single",
                    "required": False,
                    "has_default": True,
                    "default": 0,
                },
                {
                    "name": "Markets",
                    "value_type": "choice",
                    "cardinality": "list",
                    "choices": ["KR", "US"],
                    "required": False,
                },
                {
                    "name": "Include chart",
                    "value_type": "boolean",
                    "cardinality": "single",
                    "required": False,
                    "has_default": True,
                    "default": False,
                },
            ]
        )

    def test_compile_import_defaults_and_values(self):
        self.assertEqual(extract_definitions(self.schema)[0]["name"], "City")
        values = validate_inputs({"City": "Seoul", "Markets": ["KR", "US"]}, self.schema)
        self.assertEqual(values["Budget"], 0)
        self.assertIs(values["Include chart"], False)
        self.assertEqual(apply_defaults({"City": "Seoul"}, self.schema)["Budget"], 0)

    def test_rejects_missing_invalid_and_advanced_schema_import(self):
        with self.assertRaisesRegex(ValueError, "Required"):
            validate_inputs({}, self.schema)
        with self.assertRaisesRegex(ValueError, "one of"):
            validate_inputs({"City": "Seoul", "Markets": ["EU"]}, self.schema)
        self.assertIsNone(extract_definitions({"type": "object", "properties": {"x": {"oneOf": []}}}))


if __name__ == "__main__":
    unittest.main()
