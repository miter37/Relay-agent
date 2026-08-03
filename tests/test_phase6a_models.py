from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.errors import RelayError
from relay.projects.models import (
    ProjectNode,
    ProjectOutputSelection,
    ProjectSpec,
)


class Phase6aModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "home")
        self.config.init()
        self.allow_dir = self.root / "deliveries"
        self.allow_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _task_lookup(self, tid):
        return {"task_id": tid, "name": tid}

    def test_checkpoint_node_validation_passes(self):
        node = ProjectNode(
            node_id="review",
            task_id="T-1",
            checkpoint={
                "enabled": True,
                "deliver_to": [{"kind": "folder", "path": str(self.allow_dir / "out")}],
            },
        )
        spec = ProjectSpec(
            nodes=[node],
            connections=[],
            output_selection=ProjectOutputSelection([]),
        )
        spec.validate(self._task_lookup, allow_roots=[str(self.allow_dir)])
        self.assertTrue(spec.nodes[0].checkpoint["enabled"])

    def test_checkpoint_node_rejects_unknown_kind(self):
        node = ProjectNode(
            node_id="review",
            task_id="T-1",
            checkpoint={
                "enabled": True,
                "deliver_to": [{"kind": "s3", "path": "s3://bucket"}],
            },
        )
        spec = ProjectSpec(
            nodes=[node],
            connections=[],
            output_selection=ProjectOutputSelection([]),
        )
        with self.assertRaisesRegex(RelayError, "DELIVERY_KIND_UNSUPPORTED|PROJECT_INVALID"):
            spec.validate(self._task_lookup, allow_roots=[str(self.allow_dir)])

    def test_checkpoint_node_rejects_disallowed_path(self):
        node = ProjectNode(
            node_id="review",
            task_id="T-1",
            checkpoint={
                "enabled": True,
                "deliver_to": [{"kind": "folder", "path": "/etc/forbidden"}],
            },
        )
        spec = ProjectSpec(
            nodes=[node],
            connections=[],
            output_selection=ProjectOutputSelection([]),
        )
        with self.assertRaisesRegex(RelayError, "DELIVERY_PATH_NOT_ALLOWED"):
            spec.validate(self._task_lookup, allow_roots=[str(self.allow_dir)])


if __name__ == "__main__":
    unittest.main()
