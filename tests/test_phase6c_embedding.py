from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.search.embedding import NullEmbedding, get_embedding_backend


class Phase6cEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()

    def tearDown(self):
        self.temp.cleanup()

    def test_null_embedding_is_not_available(self):
        backend = NullEmbedding()
        self.assertFalse(backend.available())
        self.assertIsNone(backend.embed("hello"))

    def test_get_embedding_backend_returns_null_by_default(self):
        backend = get_embedding_backend(self.config)
        self.assertIsInstance(backend, NullEmbedding)
        self.assertFalse(backend.available())


if __name__ == "__main__":
    unittest.main()
