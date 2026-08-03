from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.lifecycle.export_service import ExportService
from relay.models import TaskSpec


class Phase6eExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.export_service = ExportService(self.db, self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_produces_valid_deterministic_zip(self):
        # Create a task
        self.engine.create_task(TaskSpec(name="T1", instructions="inst1", task_id="task-export-1"))

        out_zip = Path(self.temp.name) / "export1.zip"
        archive_path = self.export_service.export(out_path=out_zip)
        self.assertTrue(archive_path.is_file())

        # Inspect zip entries
        with zipfile.ZipFile(archive_path, "r") as zf:
            names = zf.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("tasks/task-export-1.json", names)

            # Check manifest content
            manifest = __import__("json").loads(zf.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["export_schema_version"], 1)

        # Determinism check: second export produces exact same bytes
        out_zip2 = Path(self.temp.name) / "export2.zip"
        self.export_service.export(out_path=out_zip2)
        self.assertEqual(out_zip.read_bytes(), out_zip2.read_bytes())


if __name__ == "__main__":
    unittest.main()
