import unittest

from relay.gui.json_display import render_json_html, render_json_report_html


class JsonDisplayTests(unittest.TestCase):
    def test_object_is_rendered_as_a_property_table(self):
        rendered = render_json_html(
            {"name": "Relay", "enabled": True, "count": 3, "missing": None}
        )

        self.assertIn("Field", rendered)
        self.assertIn("Value", rendered)
        self.assertIn("Type", rendered)
        self.assertIn("Relay", rendered)
        self.assertIn("boolean", rendered)
        self.assertIn("number", rendered)
        self.assertIn("—", rendered)
        self.assertNotIn('"Relay"', rendered)

    def test_array_of_objects_is_rendered_as_data_table(self):
        rendered = render_json_html(
            [
                {"name": "first", "status": "done"},
                {"name": "second", "status": "queued", "attempt": 2},
            ]
        )

        self.assertIn(">#<", rendered)
        self.assertIn(">name<", rendered)
        self.assertIn(">status<", rendered)
        self.assertIn(">attempt<", rendered)
        self.assertIn(">first<", rendered)
        self.assertIn(">queued<", rendered)
        self.assertNotIn("array-2 items", rendered)

    def test_nested_values_remain_inspectable_without_raw_json_noise(self):
        rendered = render_json_html(
            {
                "sources": ["a.json", "b.json"],
                "review": {"status": "pending", "round": 1},
            }
        )

        self.assertIn("sources", rendered)
        self.assertIn("review", rendered)
        self.assertIn("a.json", rendered)
        self.assertIn("pending", rendered)
        self.assertNotIn("[\n", rendered)

    def test_report_outline_uses_indentation_and_same_size_text(self):
        rendered = render_json_report_html(
            {
                "as of": 2026,
                "competitors": [
                    {"name": "claude code", "category": "developer"},
                    {"name": "oxiyi", "category": "bank"},
                ],
            }
        )

        self.assertIn("as of", rendered)
        self.assertIn("competitors", rendered)
        self.assertIn("[0]", rendered)
        self.assertIn("claude code", rendered)
        self.assertIn("category", rendered)
        self.assertIn("padding-left:18px", rendered)
        self.assertIn("&nbsp;&nbsp;&nbsp;&nbsp;[0]", rendered)
        self.assertIn("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;name", rendered)
        self.assertIn("font-size:13px", rendered)
        self.assertNotIn("# competitors", rendered)


if __name__ == "__main__":
    unittest.main()
