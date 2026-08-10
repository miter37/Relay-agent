"""Deterministic JSON auto-repair, added after a real antigravity Task Run failed
live (2026-08-10, "오늘의 3대 이슈 브리핑" Project) on a missing-escape mistake inside a
nested JSON-as-string artifact. No LLM, no third-party dependency: only two narrow,
unambiguous patterns are repaired (trailing comma; a nested-string key whose escaping
backslash was dropped), everything else still fails exactly as before.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.errors import RelayError
from relay.validation import _load_json_with_repair, _repair_json_text, validate_json_result


def _envelope(artifact_content: str) -> str:
    return (
        '{"schema_version": "1.0", "status": "complete", "answer": "ok", '
        '"sources": [], "uncertainties": [], "missing_items": [], '
        f'"artifacts": [{{"relative_path": "research.json", "role": "research", '
        f'"encoding": "utf-8", "content": "{artifact_content}"}}]}}'
    )


class RepairJsonTextTests(unittest.TestCase):
    def test_trailing_comma_before_closing_brace_is_removed(self):
        text = '{"a": 1, "b": 2,}'
        repaired = _repair_json_text(text)
        self.assertIsNotNone(repaired)
        self.assertEqual(json.loads(repaired), {"a": 1, "b": 2})

    def test_trailing_comma_before_closing_bracket_is_removed(self):
        text = '{"a": [1, 2, 3,]}'
        repaired = _repair_json_text(text)
        self.assertEqual(json.loads(repaired), {"a": [1, 2, 3]})

    def test_valid_json_returns_none(self):
        self.assertIsNone(_repair_json_text('{"a": 1}'))

    def test_missing_opening_escape_only_is_repaired(self):
        # Mirrors the exact first occurrence found in the live failure: closing quote
        # correctly escaped, opening quote missing its backslash.
        nested = '{\\n  \\"title\\": \\"T\\",\\n  "published_at\\": \\"2026-08-09\\"\\n}'
        text = _envelope(nested)
        repaired = _repair_json_text(text)
        self.assertIsNotNone(repaired)
        value = json.loads(repaired)
        inner = json.loads(value["artifacts"][0]["content"])
        self.assertEqual(inner["published_at"], "2026-08-09")
        self.assertEqual(inner["title"], "T")

    def test_both_quotes_missing_escape_is_repaired(self):
        # The second, more broken variant found in the same live file: neither quote
        # around the key is escaped.
        nested = '{\\n  \\"title\\": \\"T\\",\\n  "published_at": \\"2026-08-07\\"\\n}'
        text = _envelope(nested)
        repaired = _repair_json_text(text)
        self.assertIsNotNone(repaired)
        value = json.loads(repaired)
        inner = json.loads(value["artifacts"][0]["content"])
        self.assertEqual(inner["published_at"], "2026-08-07")

    def test_legitimate_top_level_key_is_never_touched(self):
        """A real top-level key is preceded by a real newline/comma, never the literal
        two characters backslash-n, so it must never be treated as needing escaping."""
        text = '{\n  "answer": "ok",\n  "sources": []\n}'
        self.assertIsNone(_repair_json_text(text))
        self.assertEqual(json.loads(text), {"answer": "ok", "sources": []})

    def test_unrelated_malformed_json_is_not_forced(self):
        """Anything outside the two known patterns must still fail exactly as before -
        no guessing."""
        text = '{"a": 1 "b": 2}'  # missing comma between two top-level values
        self.assertIsNone(_repair_json_text(text))
        with self.assertRaises(json.JSONDecodeError):
            json.loads(text)


class LoadJsonWithRepairTests(unittest.TestCase):
    def test_valid_json_returns_no_repair_marker(self):
        value, repaired_text = _load_json_with_repair('{"a": 1}')
        self.assertEqual(value, {"a": 1})
        self.assertIsNone(repaired_text)

    def test_recoverable_json_returns_repaired_text(self):
        value, repaired_text = _load_json_with_repair('{"a": 1, "b": 2,}')
        self.assertEqual(value, {"a": 1, "b": 2})
        self.assertIsNotNone(repaired_text)
        self.assertEqual(json.loads(repaired_text), {"a": 1, "b": 2})

    def test_unrecoverable_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            _load_json_with_repair('{"a": 1 "b": 2}')

    def test_real_failure_fixture_is_fully_recovered(self):
        """The exact document shape (envelope containing a nested escaped JSON string
        with two independent missing-escape mistakes) that failed live."""
        nested = (
            '{\\n  \\"researched_issues\\": [\\n    {\\n      \\"title\\": \\"A\\",\\n'
            '      \\"sources\\": [\\n        {\\n          \\"url\\": \\"https://x\\",\\n'
            '          "published_at\\": \\"2026-08-09\\"\\n        }\\n      ]\\n    },\\n'
            '    {\\n      \\"title\\": \\"B\\",\\n      \\"sources\\": [\\n        {\\n'
            '          \\"url\\": \\"https://y\\",\\n          "published_at": \\"2026-08-07\\"\\n'
            "        }\\n      ]\\n    }\\n  ]\\n}"
        )
        text = _envelope(nested)
        value, repaired_text = _load_json_with_repair(text)
        self.assertIsNotNone(repaired_text)
        inner = json.loads(value["artifacts"][0]["content"])
        dates = [s["published_at"] for issue in inner["researched_issues"] for s in issue["sources"]]
        self.assertEqual(dates, ["2026-08-09", "2026-08-07"])


class ValidateJsonResultRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _write(self, text: str) -> Path:
        path = Path(self.temp.name) / "result.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_recoverable_result_validates_and_rewrites_the_file(self):
        nested = '{\\n  \\"title\\": \\"T\\",\\n  "published_at\\": \\"2026-08-09\\"\\n}'
        path = self._write(_envelope(nested))

        value = validate_json_result(path, max_bytes=1_000_000)

        self.assertEqual(value["answer"], "ok")
        # The file on disk must now be valid JSON too (result_path is read directly
        # per SKILL.md), not just the in-memory value.
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["answer"], "ok")

    def test_unrecoverable_result_still_raises_invalid_json(self):
        path = self._write('{"a": 1 "b": 2}')
        with self.assertRaises(RelayError) as ctx:
            validate_json_result(path, max_bytes=1_000_000)
        self.assertEqual(ctx.exception.code, "INVALID_JSON")

    def test_valid_result_is_not_rewritten(self):
        text = _envelope('{\\"ok\\": true}')
        path = self._write(text)
        before = path.read_text(encoding="utf-8")

        validate_json_result(path, max_bytes=1_000_000)

        self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
