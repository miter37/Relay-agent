"""The CLI must expose enough of the authoring contract for a CLI-only caller.

An Agent that has only `relay` on PATH cannot read this repository, so the Task
input schema, the Project definition schema, and the valid Profile IDs all have
to be reachable through the CLI itself.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay.cli import _read_input_schema, build_parser
from relay.errors import RelayError
from relay.profiles import BUILTIN_PROFILES
from relay.projects.models import (
    _ALIAS_PATTERN,
    PROJECT_DEFINITION_RULES,
    PROJECT_DEFINITION_SCHEMA,
    ProjectSpec,
)


class TaskInputSchemaFlagTests(unittest.TestCase):
    def test_create_and_update_accept_an_input_schema(self):
        parser = build_parser()
        for argv in (
            ["task", "create", "--name", "x", "--input-schema", '{"type":"object"}'],
            ["task", "update", "tid", "--input-schema", '{"type":"object"}'],
        ):
            args = parser.parse_args(argv)
            self.assertEqual(args.input_schema, '{"type":"object"}')
            self.assertIsNone(args.input_schema_file)

    def test_schema_is_normalized_to_a_json_string(self):
        result = _read_input_schema('{"type":"object","properties":{"a":{"type":"string"}}}', None)
        self.assertEqual(json.loads(result)["properties"]["a"]["type"], "string")

    def test_schema_can_come_from_a_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "schema.json"
            path.write_text('{"type": "object", "properties": {"회사": {"type": "string"}}}', encoding="utf-8")
            result = _read_input_schema(None, str(path))
        self.assertIn("회사", json.loads(result)["properties"])

    def test_absent_schema_stays_absent(self):
        self.assertIsNone(_read_input_schema(None, None))

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(RelayError) as ctx:
            _read_input_schema("{not json", None)
        self.assertEqual(ctx.exception.code, "INPUT_SCHEMA_INVALID")

    def test_both_sources_at_once_is_rejected(self):
        with self.assertRaises(RelayError) as ctx:
            _read_input_schema("{}", "schema.json")
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")


class ProfileHelpTests(unittest.TestCase):
    @staticmethod
    def _profile_help_texts(parser) -> list[str]:
        """Collect the --profile help string from every subparser that exposes one."""
        found: list[str] = []

        def walk(p):
            for action in p._actions:
                if "--profile" in getattr(action, "option_strings", []) and action.help:
                    found.append(action.help)
                choices = getattr(action, "choices", None)
                if isinstance(choices, dict):
                    for sub in choices.values():
                        if hasattr(sub, "_actions"):
                            walk(sub)

        walk(parser)
        return found

    def test_profile_help_lists_every_builtin_id(self):
        helps = self._profile_help_texts(build_parser())
        self.assertTrue(helps, "no --profile option exposes help text")
        for text in helps:
            for profile in BUILTIN_PROFILES:
                self.assertIn(profile["profile_id"], text)

    def test_task_create_defaults_to_a_current_profile_id(self):
        parser = build_parser()
        args = parser.parse_args(["task", "create", "--name", "x"])
        self.assertIn(args.profile, {p["profile_id"] for p in BUILTIN_PROFILES})


class ProjectSchemaCommandTests(unittest.TestCase):
    def test_schema_subcommand_is_registered(self):
        parser = build_parser()
        args = parser.parse_args(["project", "schema", "--machine"])
        self.assertEqual(args.project_command, "schema")

    def test_schema_describes_the_fields_the_validator_enforces(self):
        properties = PROJECT_DEFINITION_SCHEMA["properties"]
        self.assertEqual(
            properties["connections"]["items"]["properties"]["to_alias"]["pattern"],
            _ALIAS_PATTERN.pattern,
        )
        for field in ("nodes", "connections", "output_selection", "failure_policy"):
            self.assertIn(field, properties)
        node = properties["nodes"]["items"]
        self.assertEqual(sorted(node["required"]), ["node_id", "task_id"])

    def test_rules_state_the_run_time_constraints_registration_cannot_catch(self):
        self.assertIn("PROJECT_ARTIFACT_AMBIGUOUS", PROJECT_DEFINITION_RULES["exactly_one_match"])
        self.assertIn("PROJECT_ARTIFACT_MISSING", PROJECT_DEFINITION_RULES["exactly_one_match"])
        self.assertIn("result", PROJECT_DEFINITION_RULES["artifact_roles"])
        self.assertIn("{node_id}__{alias}__", PROJECT_DEFINITION_RULES["input_delivery"])

    def test_a_definition_matching_the_documented_schema_validates(self):
        spec = ProjectSpec.from_dict(
            {
                "name": "documented",
                "nodes": [
                    {"node_id": "a", "task_id": "t1"},
                    {"node_id": "b", "task_id": "t2"},
                ],
                "connections": [{"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [{"node_id": "b", "role": "output"}],
            }
        )
        spec.validate(task_lookup=lambda tid: {"task_id": tid})

    def test_documented_alias_rule_is_enforced(self):
        spec = ProjectSpec.from_dict(
            {
                "name": "bad-alias",
                "nodes": [{"node_id": "a", "task_id": "t1"}, {"node_id": "b", "task_id": "t2"}],
                "connections": [{"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "input1"}],
                "output_selection": [],
            }
        )
        with self.assertRaises(RelayError) as ctx:
            spec.validate(task_lookup=lambda tid: {"task_id": tid})
        self.assertEqual(ctx.exception.code, "PROJECT_INVALID")


if __name__ == "__main__":
    unittest.main()
