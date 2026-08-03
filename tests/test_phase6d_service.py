from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.config import Config
from relay.db import Database
from relay.models import TaskSpec
from relay.notifications.service import NotificationService
from relay.notifications.sink import WebhookSink
from relay.projects.runtime import ProjectRuntime
from relay.projects.service import ProjectService
from relay.routines.runtime import RoutineRuntime
from relay.routines.service import RoutineService


class Phase6dNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("service_isolation_acknowledged", True)
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

    def test_failed_routine_run_enforces_on_failure_policy(self):
        engine = __import__("relay.engine", fromlist=["RelayEngine"]).RelayEngine(self.config, self.db)
        routine_service = RoutineService(self.config, self.db, engine)
        runtime = RoutineRuntime(self.config, self.db, engine, routine_service)
        task = engine.create_task(TaskSpec(name="notify", instructions="notify"))
        routine = routine_service.create_routine(
            {
                "name": "Notify failures",
                "target_type": "task",
                "target_id": task["task_id"],
                "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"},
                "timezone": "UTC",
                "notification_policy": {"on_failure": [{"kind": "webhook", "url": "http://127.0.0.1:9999/hook"}]},
            }
        )
        run = routine_service.run_now(routine["routine_id"])
        self.db.update_job(run["task_run_id"], status="FAILED", error_code="TEST_FAILURE")

        with patch.object(NotificationService, "notify", return_value=[]) as notify:
            runtime.tick_once()

        self.assertEqual(notify.call_args.kwargs["trigger"], "on_failure")
        self.assertEqual(notify.call_args.kwargs["routine_id"], routine["routine_id"])

    def test_notification_delivery_retries_and_logs_each_attempt(self):
        policy = {"on_failure": [{"kind": "webhook", "url": "http://127.0.0.1:9999/hook"}]}
        with patch.object(
            self.service.sink,
            "deliver",
            side_effect=[
                {"ok": False, "status_code": 503, "error": "HTTP 503"},
                {"ok": True, "status_code": 200, "error": None},
            ],
        ) as deliver:
            events = self.service.notify(trigger="on_failure", payload={"status": "failed"}, policy=policy)

        self.assertEqual(deliver.call_count, 2)
        self.assertEqual([event["attempt"] for event in events], [1, 2])
        self.assertEqual([event["status"] for event in events], ["failed", "delivered"])

    def test_failed_project_run_enforces_project_notification_policy(self):
        engine = __import__("relay.engine", fromlist=["RelayEngine"]).RelayEngine(self.config, self.db)
        project_service = ProjectService(self.db, engine)
        task = engine.create_task(TaskSpec(name="project-notify", instructions="run"))
        project = project_service.create_project(
            {
                "name": "Notify project",
                "notification_policy": {"on_failure": [{"kind": "webhook", "url": "http://127.0.0.1:9999/hook"}]},
                "nodes": [{"node_id": "n1", "task_id": task["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        run_id = project_service.create_project_run(project["project_id"])["project_run_id"]
        runtime = ProjectRuntime(self.db, engine, project_service)
        runtime.tick_once()
        step = self.db.get_project_step(run_id, "n1")
        self.db.update_job(step["active_task_run_id"], status="FAILED", error_code="TEST_FAILURE")

        with patch.object(NotificationService, "notify", return_value=[]) as notify:
            runtime.tick_once()

        self.assertEqual(notify.call_args.kwargs["trigger"], "on_failure")
        self.assertEqual(notify.call_args.kwargs["project_run_id"], run_id)


if __name__ == "__main__":
    unittest.main()
