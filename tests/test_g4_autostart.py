from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from relay.autostart import AutoStartManager
from relay.config import Config


class G4AutoStartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "relay-home"
        self.config = Config(self.home)
        self.config.init()

    def tearDown(self):
        self.temp.cleanup()

    def test_windows_enable_uses_current_user_logon_task_without_shell(self):
        runner = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        manager = AutoStartManager(
            self.config,
            platform_name="Windows",
            user_home=Path(self.temp.name) / "user",
            runner=runner,
        )

        result = manager.enable()

        self.assertTrue(result["enabled"])
        command = runner.call_args.args[0]
        self.assertEqual(command[:5], ["schtasks.exe", "/Create", "/TN", "Relay-agent", "/TR"])
        self.assertIn("daemon", command[5])
        self.assertIn(str(self.home), command[5])
        self.assertIn("/SC", command)
        self.assertIn("ONLOGON", command)
        self.assertEqual(runner.call_args.kwargs["shell"], False)

    def test_windows_disable_removes_only_relay_task(self):
        runner = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        manager = AutoStartManager(self.config, platform_name="Windows", runner=runner)

        result = manager.disable()

        self.assertFalse(result["enabled"])
        self.assertEqual(runner.call_args.args[0], ["schtasks.exe", "/Delete", "/TN", "Relay-agent", "/F"])

    def test_linux_enable_writes_user_service_and_reports_unvalidated_field(self):
        runner = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        user_home = Path(self.temp.name) / "user"
        manager = AutoStartManager(
            self.config,
            platform_name="Linux",
            user_home=user_home,
            runner=runner,
        )

        result = manager.enable()

        service_file = user_home / ".config" / "systemd" / "user" / "relay-agent.service"
        self.assertTrue(service_file.is_file())
        self.assertIn("daemon serve", service_file.read_text(encoding="utf-8"))
        self.assertTrue(result["implemented"])
        self.assertFalse(result["field_validated"])
        self.assertEqual(runner.call_args.args[0][:3], ["systemctl", "--user", "enable"])

    def test_unsupported_platform_returns_manual_start_status(self):
        manager = AutoStartManager(self.config, platform_name="Plan9", runner=Mock())

        result = manager.status()

        self.assertFalse(result["supported"])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["action"], "manual_start")


if __name__ == "__main__":
    unittest.main()
