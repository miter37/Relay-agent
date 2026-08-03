from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay.config import Config
from relay.db import Database
from relay.notifications.service import NotificationService
from relay.notifications.sink import WebhookSink


class Phase6dNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.service = NotificationService(self.db, self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_webhook_sink_allowlist_validation(self):
        sink = WebhookSink(self.config)
        # By default 127.0.0.1 is allowed
        self.assertTrue(sink.validate_url("http://127.0.0.1:8080/hook"))
        self.assertFalse(sink.validate_url("http://external-malicious.com/hook"))

    def test_notification_service_dispatches_and_logs(self):
        policy = {"on_failure": [{"kind": "webhook", "url": "http://127.0.0.1:9999/hook", "secret": "sec123"}]}
        # In test, mock webhook delivery to avoid actual network call
        events = self.service.notify(
            routine_id="r-1",
            trigger="on_failure",
            payload={"error": "failed"},
            policy=policy,
            mock_success=True,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "delivered")
        self.assertEqual(events[0]["status_code"], 200)

        logs = self.db.list_notification_events(routine_id="r-1")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["sink_url"], "http://127.0.0.1:9999/hook")


if __name__ == "__main__":
    unittest.main()
