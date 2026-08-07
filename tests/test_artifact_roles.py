"""Worker-declared Artifact roles: the contract Project connections resolve by."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.errors import RelayError
from relay.request_builder import STANDARD_JSON_SCHEMA, write_schema
from relay.validation import (
    ARTIFACT_ROLE_PATTERN,
    RESERVED_ARTIFACT_ROLES,
    normalize_declared_roles,
    scan_artifacts,
)


class DeclaredArtifactRoleTests(unittest.TestCase):
    def test_schema_offered_to_workers_permits_the_role_engine_reads(self):
        # The engine resolves Project connections by role, so the schema a Worker is
        # handed must actually allow it to declare one.
        item_schema = STANDARD_JSON_SCHEMA["properties"]["artifacts"]["items"]
        self.assertIn("role", item_schema["properties"])
        self.assertFalse(item_schema["additionalProperties"])
        self.assertNotIn("role", item_schema["required"])

    def test_written_schema_file_matches_the_documented_role_pattern(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "schema.json"
            write_schema(path)
            written = json.loads(path.read_text(encoding="utf-8"))
        role = written["properties"]["artifacts"]["items"]["properties"]["role"]
        self.assertEqual(role["pattern"], ARTIFACT_ROLE_PATTERN.pattern)

    def test_schema_constrains_schema_version_to_the_exact_literal_validation_requires(self):
        # A Worker that only sees {"type": "string"} has no way to know only "1.0"
        # is accepted and can plausibly emit "1.0.0" instead, which validate_json_result
        # then hard-rejects as SCHEMA_MISMATCH. The schema must state the literal.
        prop = STANDARD_JSON_SCHEMA["properties"]["schema_version"]
        self.assertEqual(prop.get("const"), "1.0")

    def test_declared_roles_are_normalized_and_defaulted(self):
        roles = normalize_declared_roles(
            [
                {"relative_path": "portrait.jpg", "role": "Image"},
                {"relative_path": "notes.md"},
                {"relative_path": "empty.txt", "role": ""},
                "not-a-dict",
            ]
        )
        self.assertEqual(roles, {"portrait.jpg": "image"})

    def test_reserved_result_role_cannot_be_claimed_by_a_worker(self):
        for reserved in RESERVED_ARTIFACT_ROLES:
            with self.assertRaises(RelayError) as ctx:
                normalize_declared_roles([{"relative_path": "x.txt", "role": reserved}])
            self.assertEqual(ctx.exception.code, "SCHEMA_MISMATCH")

    def test_malformed_roles_are_rejected(self):
        for bad in ("has space", "UPPER!", "9leading", "x" * 40, "-dash"):
            with self.assertRaises(RelayError) as ctx:
                normalize_declared_roles([{"relative_path": "x.txt", "role": bad}])
            self.assertEqual(ctx.exception.code, "SCHEMA_MISMATCH")

    def test_scan_applies_declared_roles_and_leaves_others_unlabelled(self):
        with tempfile.TemporaryDirectory() as temp:
            artifact_dir = Path(temp)
            (artifact_dir / "portrait.jpg").write_bytes(b"binary")
            (artifact_dir / "notes.md").write_text("plain", encoding="utf-8")
            records = scan_artifacts(artifact_dir, 10, 1_000_000, {"portrait.jpg": "image"})
        by_path = {item["relative_path"]: item for item in records}
        self.assertEqual(by_path["portrait.jpg"]["role"], "image")
        # Unlabelled files carry no role here; the engine stores them as "output".
        self.assertNotIn("role", by_path["notes.md"])


if __name__ == "__main__":
    unittest.main()
