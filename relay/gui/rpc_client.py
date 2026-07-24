from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class GuiRpcClient(QObject):
    """Small asynchronous client used by the GUI main thread."""

    response = Signal(int, object, object)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.manager = QNetworkAccessManager(self)
        self._sequence = 0
        self._replies: list[QNetworkReply] = []

    def get(self, path: str, *, timeout_ms: int = 5000) -> int:
        return self._request("GET", path, None, timeout_ms=timeout_ms)

    def post(self, path: str, payload: dict, *, timeout_ms: int = 15000) -> int:
        return self._request("POST", path, payload, timeout_ms=timeout_ms)

    def patch(self, path: str, payload: dict, *, timeout_ms: int = 15000) -> int:
        return self._request("PATCH", path, payload, timeout_ms=timeout_ms)

    def delete(self, path: str, *, timeout_ms: int = 15000) -> int:
        return self._request("DELETE", path, None, timeout_ms=timeout_ms)

    def _request(self, method: str, path: str, payload, *, timeout_ms: int) -> int:
        self._sequence += 1
        request_id = self._sequence
        request = QNetworkRequest(
            QUrl(f"http://{self.config.get('daemon_host')}:{self.config.get('daemon_port')}{path}")
        )
        request.setTransferTimeout(timeout_ms)
        token_path = self.config.path_value("runtime_root") / "daemon.token"
        if token_path.exists():
            request.setRawHeader(b"X-Relay-Token", token_path.read_text(encoding="utf-8").strip().encode("utf-8"))
        if payload is not None:
            request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if method == "GET":
            reply = self.manager.get(request)
        elif method == "POST":
            reply = self.manager.post(request, data)
        elif method == "DELETE":
            reply = self.manager.deleteResource(request)
        else:
            reply = self.manager.sendCustomRequest(request, method.encode("ascii"), data)
        self._replies.append(reply)
        reply.setProperty("relay_request_id", request_id)
        reply.setProperty("relay_timeout_ms", timeout_ms)
        reply.finished.connect(lambda: self._finished(reply))
        return request_id

    def _finished(self, reply) -> None:
        if reply in self._replies:
            self._replies.remove(reply)
        request_id = int(reply.property("relay_request_id"))
        payload: Any = None
        error: str | None = None
        try:
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            error = str(exc)
        if reply.error() != QNetworkReply.NetworkError.NoError and error is None:
            error = reply.errorString()
        self.response.emit(request_id, payload, error)
        reply.deleteLater()

    def close(self) -> None:
        for reply in self._replies:
            reply.finished.disconnect()
            reply.abort()
            reply.deleteLater()
        self._replies.clear()
