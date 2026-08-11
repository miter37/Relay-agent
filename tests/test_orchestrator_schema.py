from __future__ import annotations

import unittest

from relay.errors import RelayError
from relay.orchestrator.schema import validate_decision_payload


class ValidateDecisionPayloadTests(unittest.TestCase):
    def test_valid_retry_decision(self):
        decision = validate_decision_payload(
            {"action": "retry", "node_id": "a", "reason": "transient"}, expected_node_id="a"
        )
        self.assertEqual(decision.strategy, "retry")
        self.assertEqual(decision.node_id, "a")

    def test_valid_retry_with_worker_decision(self):
        decision = validate_decision_payload(
            {"action": "retry_with_worker", "node_id": "a", "reason": "swap", "worker": "codex"},
            expected_node_id="a",
        )
        self.assertEqual(decision.worker, "codex")

    def test_valid_rebind_connection_decision(self):
        decision = validate_decision_payload(
            {
                "action": "rebind_connection",
                "node_id": "b",
                "reason": "role mismatch",
                "connection_overrides": {"A1": "output"},
            },
            expected_node_id="b",
        )
        self.assertEqual(decision.connection_overrides, {"A1": "output"})

    def test_valid_rebind_output_role_decision(self):
        decision = validate_decision_payload(
            {"action": "rebind_output_role", "node_id": "a", "reason": "role mismatch", "output_role": "output"},
            expected_node_id="a",
        )
        self.assertEqual(decision.output_role_override, "output")

    def test_valid_give_up_decision(self):
        decision = validate_decision_payload(
            {"action": "give_up", "node_id": "a", "reason": "missing credential, not repairable"},
            expected_node_id="a",
        )
        self.assertEqual(decision.strategy, "give_up")

    def test_non_object_payload_rejected(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(["not", "an", "object"], expected_node_id="a")

    def test_unknown_field_rejected(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(
                {"action": "retry", "node_id": "a", "reason": "x", "bogus": 1}, expected_node_id="a"
            )

    def test_unknown_action_rejected(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(
                {"action": "delete_everything", "node_id": "a", "reason": "x"}, expected_node_id="a"
            )

    def test_node_id_mismatch_rejected(self):
        with self.assertRaises(RelayError) as ctx:
            validate_decision_payload({"action": "retry", "node_id": "wrong", "reason": "x"}, expected_node_id="a")
        self.assertIn("wrong", ctx.exception.message)

    def test_missing_reason_rejected(self):
        with self.assertRaises(RelayError):
            validate_decision_payload({"action": "retry", "node_id": "a", "reason": ""}, expected_node_id="a")

    def test_retry_with_worker_requires_worker(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(
                {"action": "retry_with_worker", "node_id": "a", "reason": "x"}, expected_node_id="a"
            )

    def test_rebind_connection_requires_connection_overrides(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(
                {"action": "rebind_connection", "node_id": "a", "reason": "x"}, expected_node_id="a"
            )

    def test_rebind_output_role_requires_output_role(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(
                {"action": "rebind_output_role", "node_id": "a", "reason": "x"}, expected_node_id="a"
            )

    def test_connection_overrides_must_be_string_to_string(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(
                {
                    "action": "rebind_connection",
                    "node_id": "a",
                    "reason": "x",
                    "connection_overrides": {"A1": 123},
                },
                expected_node_id="a",
            )

    def test_worker_must_be_string_or_null(self):
        with self.assertRaises(RelayError):
            validate_decision_payload(
                {"action": "retry", "node_id": "a", "reason": "x", "worker": 123}, expected_node_id="a"
            )

    def test_schema_has_no_field_capable_of_changing_output_schema_or_node_identity(self):
        from relay.orchestrator.schema import ORCHESTRATOR_DECISION_SCHEMA

        allowed = set(ORCHESTRATOR_DECISION_SCHEMA["properties"])
        self.assertNotIn("output_contract", allowed)
        self.assertNotIn("output_schema", allowed)
        self.assertNotIn("nodes", allowed)
        self.assertNotIn("final_output_node", allowed)


if __name__ == "__main__":
    unittest.main()
