from __future__ import annotations

import unittest

from relay.errors import RelayError
from relay.models import TaskSpec


class TaskSpecTests(unittest.TestCase):
    def test_validate_name_and_instructions(self):
        spec = TaskSpec(name="Report", instructions="Write it")
        spec.validate()
        row = spec.to_row()
        self.assertTrue(row["task_id"])
        self.assertEqual(row["version"], 1)

    def test_rejects_missing_name(self):
        with self.assertRaisesRegex(RelayError, "TASK_NAME_REQUIRED"):
            TaskSpec(name="   ", instructions="x").validate()


if __name__ == "__main__":
    unittest.main()
