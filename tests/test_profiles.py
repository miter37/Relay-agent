from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.profiles import ProfileStore


class ProfileStoreTests(unittest.TestCase):
    def test_builtins_and_custom_profile_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Config(Path(directory) / "home"))
            self.assertEqual(len(store.list()), 6)
            custom = store.create(
                {"name": "Internal brief", "instructions": "Use internal evidence.", "description": "Short."}
            )
            self.assertEqual(store.get(custom["profile_id"])["instructions"], "Use internal evidence.")
            updated = store.update(custom["profile_id"], {"name": "Updated", "instructions": "Verify sources."})
            self.assertEqual(updated["name"], "Updated")
            self.assertTrue(store.delete(custom["profile_id"]))
