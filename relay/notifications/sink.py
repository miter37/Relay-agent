from __future__ import annotations

import hashlib
import hmac
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from ..config import Config
from ..errors import RelayError
from ..util import canonical_json


class WebhookSink:
    def __init__(self, config: Config):
        self.config = config

    def validate_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = parsed.hostname
        if not host:
            return False
        # Allowlist: default localhost/127.0.0.1 unless configured
        allowed_hosts = set(self.config.get("notification_allowed_hosts", ["127.0.0.1", "localhost"]))
        return host in allowed_hosts

    def deliver(self, url: str, secret: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.validate_url(url):
            raise RelayError("WEBHOOK_URL_NOT_ALLOWED", f"Webhook URL is not in allow-list: {url}")

        raw_data = canonical_json(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        if secret:
            signature = hmac.new(secret.encode("utf-8"), raw_data, hashlib.sha256).hexdigest()
            headers["X-Relay-Signature"] = f"sha256={signature}"

        req = urllib.request.Request(url, data=raw_data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return {
                    "ok": True,
                    "status_code": resp.status,
                    "error": None,
                }
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "status_code": exc.code,
                "error": f"HTTP {exc.code}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "status_code": None,
                "error": str(exc),
            }
