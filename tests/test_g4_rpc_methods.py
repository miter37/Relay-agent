from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.config import Config
from relay.gui.rpc_client import GuiRpcClient


class G4RpcMethodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_patch_and_delete_use_expected_http_methods(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(Path(directory) / "relay-home")
            config.init()
            client = GuiRpcClient(config)
            with patch.object(client, "_request", return_value=7) as request:
                self.assertEqual(client.patch("/v1/schedules/sch-1", {"name": "Updated"}), 7)
                self.assertEqual(client.delete("/v1/schedules/sch-1"), 7)

            self.assertEqual(
                [call.args for call in request.call_args_list],
                [("PATCH", "/v1/schedules/sch-1", {"name": "Updated"}), ("DELETE", "/v1/schedules/sch-1", None)],
            )


if __name__ == "__main__":
    unittest.main()
