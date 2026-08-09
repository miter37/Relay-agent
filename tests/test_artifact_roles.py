"""Worker-declared Artifact roles: the contract Project connections resolve by."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.adapters.base import AdapterContext
from relay.adapters.codex import CodexAdapter, strictify_output_schema
from relay.errors import RelayError
from relay.request_builder import STANDARD_JSON_SCHEMA, write_schema
from relay.validation import (
    ARTIFACT_ROLE_PATTERN,
    RESERVED_ARTIFACT_ROLES,
    normalize_declared_roles,
    scan_artifacts,
    validate_json_result,
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


class NullRoleTests(unittest.TestCase):
    """A strict structured-output Worker cannot omit a key, so it sends null."""

    def _result(self, artifact: dict) -> dict:
        return {
            "schema_version": "1.0",
            "status": "complete",
            "answer": "ok",
            "sources": [],
            "uncertainties": [],
            "missing_items": [],
            "artifacts": [artifact],
        }

    def _validate(self, value: dict) -> dict:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return validate_json_result(path, 1_000_000)

    def test_null_role_is_accepted_and_means_no_role_declared(self):
        artifact = {"relative_path": "a.txt", "role": None, "encoding": "utf-8", "content": "x"}
        validated = self._validate(self._result(artifact))
        self.assertIsNone(validated["artifacts"][0]["role"])
        # Null must resolve exactly like an absent key: no declared role at all.
        self.assertEqual(normalize_declared_roles(validated["artifacts"]), {})

    def test_null_summary_is_accepted(self):
        value = self._result({"relative_path": "a.txt", "encoding": "utf-8", "content": "x"})
        value["summary"] = None
        self.assertIsNone(self._validate(value)["summary"])

    def test_a_malformed_non_null_role_is_still_rejected(self):
        artifact = {"relative_path": "a.txt", "role": "Has Space", "encoding": "utf-8", "content": "x"}
        with self.assertRaises(RelayError) as ctx:
            self._validate(self._result(artifact))
        self.assertEqual(ctx.exception.code, "SCHEMA_MISMATCH")


class StrictOutputSchemaTests(unittest.TestCase):
    """OpenAI strict mode rejects the whole request unless the required/properties
    invariant holds at every nesting level, which is what broke Codex's deep audit."""

    @staticmethod
    def _objects(node, path="(root)"):
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            yield path, node
        for key, prop in (node.get("properties") or {}).items():
            yield from StrictOutputSchemaTests._objects(prop, f"{path}.{key}")
        if isinstance(node.get("items"), dict):
            yield from StrictOutputSchemaTests._objects(node["items"], f"{path}.items")

    def test_every_nesting_level_satisfies_the_strict_invariant(self):
        schema = json.loads(json.dumps(STANDARD_JSON_SCHEMA))
        strictify_output_schema(schema)
        checked = [path for path, _ in self._objects(schema)]
        for path, node in self._objects(schema):
            self.assertEqual(
                set((node.get("properties") or {}).keys()),
                set(node.get("required") or []),
                f"{path} would be rejected with invalid_json_schema",
            )
        # The nested artifact item is the level the shallow implementation missed.
        self.assertIn("(root).artifacts.items", checked)

    def test_optional_properties_become_nullable_instead_of_mandatory_values(self):
        schema = json.loads(json.dumps(STANDARD_JSON_SCHEMA))
        strictify_output_schema(schema)
        role = schema["properties"]["artifacts"]["items"]["properties"]["role"]
        self.assertEqual(role["type"], ["string", "null"])
        self.assertEqual(schema["properties"]["summary"]["type"], ["string", "null"])
        # Genuinely required fields keep their plain type.
        self.assertEqual(schema["properties"]["answer"]["type"], "string")
        self.assertEqual(
            schema["properties"]["artifacts"]["items"]["properties"]["relative_path"]["type"],
            "string",
        )

    def test_the_shared_worker_schema_is_not_mutated(self):
        # Only Codex's copy is strictified; every other Worker keeps role optional.
        before = json.dumps(STANDARD_JSON_SCHEMA, sort_keys=True)
        strictify_output_schema(json.loads(before))
        self.assertEqual(json.dumps(STANDARD_JSON_SCHEMA, sort_keys=True), before)
        self.assertNotIn("role", STANDARD_JSON_SCHEMA["properties"]["artifacts"]["items"]["required"])

    def test_codex_build_command_writes_a_strict_schema_file(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            schema_file = workspace / "schema.json"
            write_schema(schema_file)
            ctx = AdapterContext(
                job_id="probe",
                workspace=workspace,
                request_file=workspace / "request.md",
                result_file=workspace / "result.json",
                artifact_dir=workspace / "artifacts",
                schema_file=schema_file,
                result_format="json",
                profile="doctor",
                model=None,
                config={},
            )
            adapter = CodexAdapter({}, workspace)
            adapter.executable = lambda: "/usr/bin/codex"  # type: ignore[method-assign]
            adapter.build_command(ctx)
            written = json.loads(schema_file.read_text(encoding="utf-8"))
        item = written["properties"]["artifacts"]["items"]
        self.assertIn("role", item["required"])
        self.assertEqual(item["properties"]["role"]["type"], ["string", "null"])


if __name__ == "__main__":
    unittest.main()
