from __future__ import annotations

import unittest

from relay.task_interface import (
    diagnose_output_artifacts,
    diagnose_project_interfaces,
    normalize_interface,
    task_interface,
)


def _lookup(tasks: dict[str, dict]):
    return lambda task_id: tasks.get(task_id)


class TaskInterfaceTests(unittest.TestCase):
    def test_legacy_task_has_system_result_without_becoming_declared(self):
        interface = task_interface({"task_id": "legacy", "result_format": "json"})

        self.assertFalse(interface.declared)
        self.assertEqual([port.name for port in interface.outputs], ["result"])
        self.assertEqual(interface.output("result").formats, ("application/json",))

    def test_normalize_interface_rejects_duplicate_names_and_reserved_result(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            normalize_interface(
                {
                    "artifact_inputs": [{"name": "research"}, {"name": "research"}],
                    "outputs": [],
                }
            )
        with self.assertRaisesRegex(ValueError, "reserved"):
            normalize_interface({"artifact_inputs": [], "outputs": [{"role": "result"}]})

    def test_named_connection_is_checked_against_declared_ports(self):
        tasks = {
            "research": {
                "task_id": "research",
                "result_format": "json",
                "output_contract": {
                    "interface_version": 1,
                    "outputs": [{"role": "report", "produces": ["application/json"], "required": True}],
                },
            },
            "writer": {
                "task_id": "writer",
                "output_contract": {
                    "interface_version": 1,
                    "artifact_inputs": [
                        {"name": "research", "accepts": ["application/json"], "required": True}
                    ],
                },
            },
        }
        report = diagnose_project_interfaces(
            {
                "nodes": [
                    {"node_id": "r", "task_id": "research"},
                    {"node_id": "w", "task_id": "writer"},
                ],
                "connections": [
                    {
                        "from_node": "r",
                        "from_output": "report",
                        "to_node": "w",
                        "to_input": "research",
                    }
                ],
            },
            _lookup(tasks),
        )

        self.assertTrue(report.valid)
        self.assertEqual(report.errors, [])

    def test_unknown_output_has_machine_location_and_suggestions(self):
        task = {
            "task_id": "research",
            "output_contract": {
                "interface_version": 1,
                "outputs": [{"role": "report"}],
            },
        }
        report = diagnose_project_interfaces(
            {
                "nodes": [{"node_id": "r", "task_id": "research"}, {"node_id": "w", "task_id": "research"}],
                "connections": [
                    {
                        "from_node": "r",
                        "from_output": "market_data",
                        "to_node": "w",
                        "to_input": "missing",
                    }
                ],
            },
            _lookup({"research": task}),
        )

        self.assertFalse(report.valid)
        self.assertEqual(report.errors[0].code, "CONNECTION_OUTPUT_UNKNOWN")
        self.assertEqual(report.errors[0].path, "/connections/0/from_output")
        self.assertIn("report", report.errors[0].suggestions)

    def test_legacy_alias_is_warning_and_does_not_break_existing_project(self):
        task = {
            "task_id": "task",
            "output_contract": {
                "interface_version": 1,
                "artifact_inputs": [{"name": "research"}],
            },
        }
        report = diagnose_project_interfaces(
            {
                "nodes": [{"node_id": "a", "task_id": "task"}, {"node_id": "b", "task_id": "task"}],
                "connections": [
                    {"from_node": "a", "from_role": "result", "to_node": "b", "to_alias": "A1"}
                ],
            },
            _lookup({"task": task}),
        )

        self.assertTrue(report.valid)
        self.assertEqual([item.code for item in report.warnings], ["CONNECTION_LEGACY_ALIAS"])
        self.assertIn("research", report.warnings[0].suggestions)

    def test_incompatible_named_formats_are_blocking(self):
        tasks = {
            "source": {
                "task_id": "source",
                "output_contract": {
                    "interface_version": 1,
                    "outputs": [{"role": "image", "produces": ["image/png"]}],
                },
            },
            "target": {
                "task_id": "target",
                "output_contract": {
                    "interface_version": 1,
                    "artifact_inputs": [{"name": "document", "accepts": ["text/markdown"]}],
                },
            },
        }
        report = diagnose_project_interfaces(
            {
                "nodes": [
                    {"node_id": "s", "task_id": "source"},
                    {"node_id": "t", "task_id": "target"},
                ],
                "connections": [
                    {"from_node": "s", "from_output": "image", "to_node": "t", "to_input": "document"}
                ],
            },
            _lookup(tasks),
        )

        self.assertFalse(report.valid)
        self.assertEqual(report.errors[0].code, "CONNECTION_FORMAT_INCOMPATIBLE")

    def test_output_contract_requires_declared_role_and_format(self):
        task = {
            "result_format": "json",
            "output_contract": {
                "interface_version": 1,
                "outputs": [{"role": "report", "required": True, "produces": ["text/html"]}],
            },
        }
        missing = diagnose_output_artifacts(task, [])
        self.assertFalse(missing.valid)
        self.assertEqual(missing.errors[0].code, "OUTPUT_CONTRACT_MISSING")
        wrong_format = diagnose_output_artifacts(
            task,
            [{"role": "report", "mime_type": "application/json", "relative_path": "report.json"}],
        )
        self.assertFalse(wrong_format.valid)
        self.assertEqual(wrong_format.errors[0].code, "OUTPUT_CONTRACT_FORMAT")

    def test_output_contract_allows_many_and_any_file(self):
        task = {
            "output_contract": {
                "interface_version": 1,
                "outputs": [{"role": "sources", "cardinality": "many", "produces": ["Any file"]}],
            }
        }
        report = diagnose_output_artifacts(
            task,
            [
                {"role": "sources", "mime_type": "text/plain", "relative_path": "a.txt"},
                {"role": "sources", "mime_type": "application/json", "relative_path": "b.json"},
            ],
        )
        self.assertTrue(report.valid)


if __name__ == "__main__":
    unittest.main()
