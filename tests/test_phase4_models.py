from __future__ import annotations

import unittest

from relay.errors import RelayError
from relay.projects.models import (
    ProjectConnection,
    ProjectNode,
    ProjectOutputSelection,
    ProjectSpec,
)


def _ok(tasks):
    table = {tid: {"name": tid, "version": 1, "instructions": "x"} for tid in tasks}
    return lambda tid: table.get(tid)


class ProjectSpecTests(unittest.TestCase):
    def test_valid_sequential_project_passes(self):
        spec = ProjectSpec(
            nodes=[ProjectNode("a", "T-A"), ProjectNode("b", "T-B")],
            connections=[ProjectConnection("a", "raw", "b", "A1")],
            output_selection=ProjectOutputSelection(items=[{"node_id": "b", "role": "final"}]),
            failure_policy="stop",
        )
        spec.validate(_ok(["T-A", "T-B"]))
        self.assertEqual(spec.topological_order(), ["a", "b"])
        self.assertEqual(spec.root_nodes(), ["a"])

    def test_self_loop_rejected(self):
        spec = ProjectSpec(
            nodes=[ProjectNode("a", "T-A")],
            connections=[ProjectConnection("a", "raw", "a", "A1")],
            output_selection=ProjectOutputSelection([]),
            failure_policy="stop",
        )
        with self.assertRaisesRegex(RelayError, "PROJECT_INVALID"):
            spec.validate(_ok(["T-A"]))

    def test_cycle_rejected(self):
        spec = ProjectSpec(
            nodes=[ProjectNode("a", "T-A"), ProjectNode("b", "T-B")],
            connections=[
                ProjectConnection("a", "raw", "b", "A1"),
                ProjectConnection("b", "raw", "a", "A1"),
            ],
            output_selection=ProjectOutputSelection([]),
            failure_policy="stop",
        )
        with self.assertRaisesRegex(RelayError, "PROJECT_CYCLE"):
            spec.validate(_ok(["T-A", "T-B"]))

    def test_duplicate_alias_rejected(self):
        spec = ProjectSpec(
            nodes=[ProjectNode("a", "T-A"), ProjectNode("b", "T-B")],
            connections=[
                ProjectConnection("a", "raw", "b", "A1"),
                ProjectConnection("a", "other", "b", "A1"),
            ],
            output_selection=ProjectOutputSelection([]),
            failure_policy="stop",
        )
        with self.assertRaisesRegex(RelayError, "PROJECT_INPUT_CONFLICT"):
            spec.validate(_ok(["T-A", "T-B"]))

    def test_invalid_alias_rejected(self):
        spec = ProjectSpec(
            nodes=[ProjectNode("a", "T-A"), ProjectNode("b", "T-B")],
            connections=[ProjectConnection("a", "raw", "b", "B1")],
            output_selection=ProjectOutputSelection([]),
            failure_policy="stop",
        )
        with self.assertRaisesRegex(RelayError, "PROJECT_INVALID"):
            spec.validate(_ok(["T-A", "T-B"]))

    def test_missing_task_rejected(self):
        spec = ProjectSpec(
            nodes=[ProjectNode("a", "T-MISSING")],
            connections=[],
            output_selection=ProjectOutputSelection([]),
            failure_policy="stop",
        )
        with self.assertRaisesRegex(RelayError, "PROJECT_TASK_MISSING"):
            spec.validate(lambda _t: None)

    def test_diamond_topological_order_is_deterministic(self):
        spec = ProjectSpec(
            nodes=[
                ProjectNode("root", "T-R"),
                ProjectNode("a", "T-A"),
                ProjectNode("b", "T-B"),
                ProjectNode("join", "T-J"),
            ],
            connections=[
                ProjectConnection("root", "raw", "a", "A1"),
                ProjectConnection("root", "raw", "b", "A1"),
                ProjectConnection("a", "out", "join", "A1"),
                ProjectConnection("b", "out", "join", "A2"),
            ],
            output_selection=ProjectOutputSelection([]),
            failure_policy="stop",
        )
        spec.validate(_ok(["T-R", "T-A", "T-B", "T-J"]))
        order = spec.topological_order()
        self.assertEqual(order[0], "root")
        self.assertEqual(order[-1], "join")
        self.assertEqual(set(order[1:3]), {"a", "b"})

    def test_to_snapshot_is_canonical_and_round_trips(self):
        spec = ProjectSpec(
            nodes=[ProjectNode("a", "T-A"), ProjectNode("b", "T-B")],
            connections=[ProjectConnection("a", "raw", "b", "A1")],
            output_selection=ProjectOutputSelection(items=[{"node_id": "b", "role": "final"}]),
            failure_policy="stop",
        )
        round = ProjectSpec.from_dict(__import__("json").loads(spec.to_snapshot()))
        self.assertEqual(round.topological_order(), ["a", "b"])

    def test_manual_wait_node_does_not_require_a_registered_task(self):
        spec = ProjectSpec.from_dict(
            {
                "name": "pause",
                "nodes": [{"node_id": "pause", "type": "wait", "wait": {"mode": "manual"}}],
                "connections": [],
                "output_selection": [],
            }
        )
        spec.validate(lambda _task: None)
        round_trip = ProjectSpec.from_dict(__import__("json").loads(spec.to_snapshot()))
        self.assertEqual(round_trip.nodes[0].node_type, "wait")
        self.assertEqual(round_trip.nodes[0].wait["mode"], "manual")


if __name__ == "__main__":
    unittest.main()
