from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Config
from .errors import RelayError


class RPCClient:
    def __init__(self, config: Config):
        self.config = config
        self.base = f"http://{config.get('daemon_host')}:{config.get('daemon_port')}"
        self.token_path = config.path_value("runtime_root") / "daemon.token"
        self.token = self.token_path.read_text(encoding="utf-8").strip() if self.token_path.exists() else ""

    def request(self, method: str, path: str, payload: Any = None, timeout: int = 15) -> Any:
        if self.token_path.exists():
            self.token = self.token_path.read_text(encoding="utf-8").strip()
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "X-Relay-Token": self.token},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("error_code"):
                raise RelayError(
                    str(payload["error_code"]),
                    str(payload.get("error_message") or f"Relay daemon returned HTTP {exc.code}"),
                    bool(payload.get("retryable", False)),
                    payload.get("details"),
                ) from exc
            raise RelayError("DAEMON_UNAVAILABLE", f"Relay daemon returned HTTP {exc.code}", True) from exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError) as exc:
            raise RelayError("DAEMON_UNAVAILABLE", f"Relay daemon is unavailable: {exc}", True) from exc

    def health(self) -> bool:
        try:
            result = self.request("GET", "/health", timeout=2)
            return bool(result.get("ok"))
        except RelayError:
            return False

    def wait_until_healthy(self, seconds: float = 10) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.health():
                return True
            time.sleep(0.25)
        return False
