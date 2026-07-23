from __future__ import annotations

import os
import platform
import plistlib
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from .config import Config
from .errors import RelayError
from .util import entrypoint_command


class AutoStartManager:
    TASK_NAME = "Relay-agent"
    SERVICE_NAME = "relay-agent.service"
    LAUNCH_AGENT = "com.relay.agent.plist"

    def __init__(
        self,
        config: Config,
        *,
        platform_name: str | None = None,
        user_home: Path | None = None,
        runner: Callable = subprocess.run,
    ):
        self.config = config
        self.platform_name = platform_name or platform.system()
        self.user_home = user_home or Path.home()
        self.runner = runner

    @property
    def supported(self) -> bool:
        return self.platform_name in {"Windows", "Linux", "Darwin"}

    @property
    def field_validated(self) -> bool:
        return self.platform_name == "Windows"

    def _command(self) -> list[str]:
        return entrypoint_command(["--home", str(self.config.home), "daemon", "serve"])

    def _run(self, command: list[str]) -> None:
        result = self.runner(command, check=False, capture_output=True, text=True, shell=False)
        if getattr(result, "returncode", 1) != 0:
            detail = getattr(result, "stderr", "") or getattr(result, "stdout", "") or "command failed"
            raise RelayError("AUTOSTART_FAILED", str(detail).strip(), True)

    def _windows_action(self) -> str:
        return subprocess.list2cmdline(self._command())

    def _linux_service_path(self) -> Path:
        return self.user_home / ".config" / "systemd" / "user" / self.SERVICE_NAME

    def _macos_plist_path(self) -> Path:
        return self.user_home / "Library" / "LaunchAgents" / self.LAUNCH_AGENT

    def status(self) -> dict[str, object]:
        if not self.supported:
            return {
                "platform": self.platform_name,
                "supported": False,
                "implemented": False,
                "field_validated": False,
                "enabled": False,
                "action": "manual_start",
                "warning": "Relay auto-start is unavailable on this platform.",
            }
        return {
            "platform": self.platform_name,
            "supported": True,
            "implemented": True,
            "field_validated": self.field_validated,
            "enabled": bool(self.config.get("autostart_enabled", False)),
            "action": "managed_start" if self.config.get("autostart_enabled", False) else "manual_start",
            "warning": None if self.field_validated else "Auto-start requires validation on this target machine.",
        }

    def enable(self) -> dict[str, object]:
        if not self.supported:
            return self.status()
        if self.platform_name == "Windows":
            self._run(
                [
                    "schtasks.exe",
                    "/Create",
                    "/TN",
                    self.TASK_NAME,
                    "/TR",
                    self._windows_action(),
                    "/SC",
                    "ONLOGON",
                    "/F",
                ]
            )
        elif self.platform_name == "Linux":
            path = self._linux_service_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "[Unit]\nDescription=Relay-agent daemon\n\n"
                "[Service]\nType=simple\n"
                f"ExecStart={shlex.join(self._command())}\nRestart=on-failure\n\n"
                "[Install]\nWantedBy=default.target\n",
                encoding="utf-8",
            )
            self._run(["systemctl", "--user", "daemon-reload"])
            self._run(["systemctl", "--user", "enable", "--now", self.SERVICE_NAME])
        else:
            path = self._macos_plist_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                plistlib.dumps(
                    {
                        "Label": "com.relay.agent",
                        "ProgramArguments": self._command(),
                        "RunAtLoad": True,
                        "KeepAlive": True,
                    }
                )
            )
            self._run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)])
        self.config.set("autostart_enabled", True)
        return self.status()

    def disable(self) -> dict[str, object]:
        if not self.supported:
            return self.status()
        if self.platform_name == "Windows":
            self._run(["schtasks.exe", "/Delete", "/TN", self.TASK_NAME, "/F"])
        elif self.platform_name == "Linux":
            self._run(["systemctl", "--user", "disable", "--now", self.SERVICE_NAME])
            self._linux_service_path().unlink(missing_ok=True)
        else:
            self._run(["launchctl", "bootout", f"gui/{os.getuid()}", str(self._macos_plist_path())])
            self._macos_plist_path().unlink(missing_ok=True)
        self.config.set("autostart_enabled", False)
        return self.status()
