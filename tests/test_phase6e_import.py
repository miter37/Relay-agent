from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.engine import RelayEngine
from relay.models import TaskSpec
from relay.lifecycle.export_service import ExportService
from relay.lifecycle.import_service import ImportService


class Phase6eImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home1 = Path(self.temp.name) / "home1"
        self.config1 = Config(self.home1)
        self.config1.init()
        self.db1 = Database(self.config1.path_value("database_path"))
        self.engine1 = RelayEngine(self.config1, self.db1)

        self.home2 = Path(self.temp.name) / "home2"
        self.config2 = Config(self.home2)
        self.config2.init()
        self.db2 = Database(self.config2.path_value("database_path"))

        self.export_service = ExportService(self.db1, self.config1)
        self.import_service = ImportService(self.db2, self.config2)

    def tearDown(self):
        self.temp.cleanup()

    def test_import_round_trip(self):
        # Create task in db1
        self.engine1.create_task(TaskSpec(name="Importable Task", instructions="inst", task_id="t-imp-1"))

        # Export from db1
        zip_path = Path(self.temp.name) / "export.zip"
        self.export_service.export(out_path=zip_path)

        # Import into db2
        res = self.import_service.import_archive(zip_path, conflict="skip")
        self.assertTrue(res["ok"])
        self.assertEqual(res["imported_tasks"], 1)

        # Verify task exists in db2
        task2 = self.db2.get_task("t-imp-1")
        self.assertIsNotNone(task2)
        self.assertEqual(task2["name"], "Importable Task")

    def test_import_conflict_resolution(self):
        # Create task in db1 and export
        self.engine1.create_task(TaskSpec(name="Conflict Task", instructions="v1", task_id="t-conf-1"))
        zip_path = Path(self.temp.name) / "export.zip"
        self.export_service.export(out_path=zip_path)

        # Pre-create task with same name in db2
        engine2 = RelayEngine(self.config2, self.db2)
        engine2.create_task(TaskSpec(name="Conflict Task", instructions="v2", task_id="t-conf-2"))

        # Import with conflict=skip
        res_skip = self.import_service.import_archive(zip_path, conflict="skip")
        self.assertEqual(res_skip["imported_tasks"], 0)
        self.assertEqual(res_skip["conflicts"], 1)


if __name__ == "__main__":
    unittest.main()
