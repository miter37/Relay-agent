from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest


class GuiRpcClient(QObject):
    """Small asynchronous client used by the GUI main thread."""

    response = Signal(int, object, object)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.manager = QNetworkAccessManager(self)
        self._sequence = 0

    def get(self, path: str, *, timeout_ms: int = 5000) -> int:
        self._sequence += 1
        request_id = self._sequence
        request = QNetworkRequest(
            QUrl(f"http://{self.config.get('daemon_host')}:{self.config.get('daemon_port')}{path}")
        )
        token_path = self.config.path_value("runtime_root") / "daemon.token"
        if token_path.exists():
            request.setRawHeader(b"X-Relay-Token", token_path.read_text(encoding="utf-8").strip().encode("utf-8"))
        reply = self.manager.get(request)
        reply.setProperty("relay_request_id", request_id)
        reply.setProperty("relay_timeout_ms", timeout_ms)
        reply.finished.connect(lambda: self._finished(reply))
        return request_id

    def _finished(self, reply) -> None:
        request_id = int(reply.property("relay_request_id"))
        payload: Any = None
        error: str | None = None
        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            error = str(exc)
        if reply.error() and error is None:
            error = reply.errorString()
        self.response.emit(request_id, payload, error)
        reply.deleteLater()
