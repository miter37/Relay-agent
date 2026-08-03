from __future__ import annotations

import hashlib
from typing import Any

from ..config import Config
from ..db import Database
from ..errors import RelayError
from ..util import canonical_json, new_job_id, utc_now
from .sink import WebhookSink


class NotificationService:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self.sink = WebhookSink(config)

    def notify(
        self,
        *,
        routine_id: str | None = None,
        project_run_id: str | None = None,
        trigger: str,
        payload: dict[str, Any],
        policy: dict[str, Any] | None = None,
        mock_success: bool = False,
    ) -> list[dict[str, Any]]:
        if not policy:
            return []

        sinks = policy.get(trigger) or []
        if not sinks:
            return []

        events = []
        payload_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

        for sink_item in sinks:
            kind = sink_item.get("kind", "webhook")
            if kind != "webhook":
                continue

            url = sink_item.get("url")
            secret = sink_item.get("secret")
            if not url:
                continue

            max_attempts = 1 if mock_success else max(1, int(self.config.get("notification_retry_attempts", 3)))
            for attempt in range(1, max_attempts + 1):
                if mock_success:
                    res = {"ok": True, "status_code": 200, "error": None}
                else:
                    try:
                        res = self.sink.deliver(url, secret, payload)
                    except RelayError as exc:
                        res = {"ok": False, "status_code": None, "error": exc.message}

                event_row = {
                    "event_id": new_job_id(),
                    "routine_id": routine_id,
                    "project_run_id": project_run_id,
                    "trigger_type": trigger,
                    "sink_url": url,
                    "status": "delivered" if res["ok"] else "failed",
                    "status_code": res["status_code"],
                    "attempt": attempt,
                    "error": res["error"],
                    "payload_hash": payload_hash,
                    "created_at": utc_now(),
                }
                self.db.create_notification_event(event_row)
                events.append(event_row)
                if res["ok"]:
                    break

        return events
