from __future__ import annotations

import socket
import tempfile
import threading
import unittest
from pathlib import Path

from relay.config import Config
from relay.daemon import RelayDaemon
from relay.db import Database
from relay.engine import RelayEngine
from relay.errors import RelayError
from relay.models import TaskSpec
from relay.projects.service import ProjectService
from relay.rpc import RPCClient


class ProjectAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.db = Database(self.config.path_value("database_path"))
        self.engine = RelayEngine(self.config, self.db)
        self.service = ProjectService(self.db, self.engine)
        # Confirm that service methods are usable via engine proxy.
        self.ta = self.engine.create_task(TaskSpec(name="TA", instructions="a"))
        self.tb = self.engine.create_task(TaskSpec(name="TB", instructions="b"))

    def tearDown(self):
        self.temp.cleanup()

    def test_service_run_create_recipes(self):
        project = self.service.create_project(
            {
                "name": "P",
                "nodes": [
                    {"node_id": "a", "task_id": self.ta["task_id"]},
                    {"node_id": "b", "task_id": self.tb["task_id"]},
                ],
                "connections": [
                    {"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"},
                ],
                "output_selection": [{"node_id": "b", "role": "final_report"}],
            }
        )
        run_payload = self.service.create_project_run(project["project_id"])
        steps = self.db.list_project_steps(run_payload["project_run_id"])
        statuses = {s["node_id"]: s["status"] for s in steps}
        self.assertEqual(statuses["a"], "ready")
        self.assertEqual(statuses["b"], "pending")

    def test_invalid_definition_rejected(self):

        def _raise(_tid):
            raise RelayError("PROJECT_TASK_MISSING", "missing")

        # Test that invalid definitions are rejected
        with self.assertRaisesRegex(RelayError, "PROJECT_TASK_MISSING"):
            self.service.create_project(
                {
                    "name": "bad",
                    "nodes": [{"node_id": "x", "task_id": "no-such-task"}],
                    "connections": [],
                    "output_selection": [],
                }
            )

        # Test that valid projections are accepted
        good = self.service.create_project(
            {
                "name": "good",
                "nodes": [
                    {"node_id": "a", "task_id": self.ta["task_id"]},
                    {"node_id": "b", "task_id": self.tb["task_id"]},
                ],
                "connections": [],
                "output_selection": [],
            }
        )
        self.assertTrue(good["project_id"])

    def test_receipt_shape(self):
        project = self.service.create_project(
            {
                "name": "P",
                "nodes": [
                    {"node_id": "a", "task_id": self.ta["task_id"]},
                    {"node_id": "b", "task_id": self.tb["task_id"]},
                ],
                "connections": [
                    {"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"},
                ],
                "output_selection": [{"node_id": "b", "role": "final_report"}],
            }
        )
        run_payload = self.service.create_project_run(project["project_id"])
        receipt = self.service.project_run_receipt(run_payload["project_run_id"])
        self.assertEqual(receipt["project_id"], project["project_id"])
        self.assertEqual(receipt["status"], "running")
        self.assertEqual(len(receipt["steps"]), 2)
        self.assertEqual(receipt["steps"][0]["node_id"], "a")
        self.assertEqual(receipt["steps"][1]["node_id"], "b")


class DaemonProjectRouteTests(unittest.TestCase):
    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("daemon_port", self._free_port())
        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()
        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(5.0))

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except Exception:
                pass
            self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_health_lists_project_runtime_capability(self):
        health = self.client.request("GET", "/health")
        self.assertIn("project-runtime", health["capabilities"])

    def test_daemon_create_and_run_project(self):
        from relay.db import Database
        from relay.engine import RelayEngine
        from relay.models import TaskSpec

        db = Database(self.config.path_value("database_path"))
        engine = RelayEngine(self.config, db)
        ta = engine.create_task(TaskSpec(name="TA", instructions="a"))
        tb = engine.create_task(TaskSpec(name="TB", instructions="b"))
        project = self.client.request(
            "POST",
            "/v1/projects",
            {
                "name": "P",
                "nodes": [
                    {"node_id": "a", "task_id": ta["task_id"]},
                    {"node_id": "b", "task_id": tb["task_id"]},
                ],
                "connections": [
                    {"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"},
                ],
                "output_selection": [],
            },
        )
        self.assertTrue(project["ok"])
        project_id = project["project"]["project_id"]
        run = self.client.request("POST", f"/v1/projects/{project_id}/run", {})
        self.assertTrue(run["ok"])
        # Project Run list
        runs = self.client.request("GET", f"/v1/projects/{project_id}/runs")
        self.assertEqual(len(runs["project_runs"]), 1)
        # Steps
        steps = self.client.request("GET", f"/v1/project-runs/{runs['project_runs'][0]['project_run_id']}/steps")
        self.assertEqual(len(steps["steps"]), 2)
        # Receipt
        receipt = self.client.request("GET", f"/v1/project-runs/{runs['project_runs'][0]['project_run_id']}/receipt")
        self.assertEqual(receipt["receipt"]["project_id"], project_id)


class DaemonProjectRunRouteTests(unittest.TestCase):
    """Regression for Phase 4 daemon-route readiness.

    The Project Run retry / cancel / partial-reexecute and the Routine preview
    handlers were previously wired under do_GET while the CLI sends POST. This test
    pins the method/path contract so future changes do not silently regress.
    """

    @staticmethod
    def _free_port() -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("daemon_port", self._free_port())
        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()
        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(5.0))

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except RelayError:
                pass
            self.thread.join(timeout=5)
        self.temp.cleanup()

    @property
    def engine(self):
        return self.daemon.engine

    def _make_project_run(self):
        ta = self.engine.create_task(TaskSpec(name="TA", instructions="a"))
        tb = self.engine.create_task(TaskSpec(name="TB", instructions="b"))
        project = self.engine.project_service.create_project(
            {
                "name": "P",
                "nodes": [
                    {"node_id": "a", "task_id": ta["task_id"]},
                    {"node_id": "b", "task_id": tb["task_id"]},
                ],
                "connections": [{"from_node": "a", "from_role": "out", "to_node": "b", "to_alias": "A1"}],
                "output_selection": [{"node_id": "b", "role": "final_report"}],
            }
        )
        run = self.client.request("POST", f"/v1/projects/{project['project_id']}/run", {})
        return run["project_run_id"]

    def test_retry_route_uses_post(self):
        project_run_id = self._make_project_run()
        # GET on the mutation endpoint should not silently succeed; the
        # daemon no longer has a GET handler for project_run_retry.
        with self.assertRaises(RelayError):
            self.client.request("GET", f"/v1/project-runs/{project_run_id}/retry")
        # POST should reach the handler. The fresh Project Run is still running
        # (its child Task Runs are queued), so a retry is rejected with
        # PROJECT_RETRY_INVALID. Any PROJECT_-prefixed error proves the POST
        # reached project_run_retry rather than a generic 404.
        with self.assertRaises(RelayError) as ctx:
            self.client.request("POST", f"/v1/project-runs/{project_run_id}/retry", {})
        self.assertTrue(ctx.exception.code.startswith("PROJECT"))

    def test_cancel_route_uses_post(self):
        project_run_id = self._make_project_run()
        with self.assertRaises(RelayError):
            self.client.request("GET", f"/v1/project-runs/{project_run_id}/cancel")
        cancelled = self.client.request("POST", f"/v1/project-runs/{project_run_id}/cancel")
        self.assertTrue(cancelled.get("ok"))

    def test_partial_reexecute_route_uses_post(self):
        project_run_id = self._make_project_run()
        with self.assertRaises(RelayError):
            self.client.request("GET", f"/v1/project-runs/{project_run_id}/partial-reexecute")
        # POST without from_node must reject with INVALID_REQUEST, do not silently
        # fall through to any other handler.
        with self.assertRaises(RelayError):
            self.client.request(
                "POST",
                f"/v1/project-runs/{project_run_id}/partial-reexecute",
                {"cascade": True},
            )
        ok = self.client.request(
            "POST",
            f"/v1/project-runs/{project_run_id}/partial-reexecute",
            {"from_node": "b", "cascade": False},
        )
        self.assertTrue(ok.get("ok"))

    def test_partial_reexecute_accepts_instruction_addendum(self):
        project_run_id = self._make_project_run()
        ok = self.client.request(
            "POST",
            f"/v1/project-runs/{project_run_id}/partial-reexecute",
            {"from_node": "b", "cascade": False, "instruction_addendum": "please double-check the totals"},
        )
        self.assertTrue(ok.get("ok"))
        with self.assertRaises(RelayError) as ctx:
            self.client.request(
                "POST",
                f"/v1/project-runs/{project_run_id}/partial-reexecute",
                {"from_node": "b", "cascade": False, "instruction_addendum": 123},
            )
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")
        with self.assertRaises(RelayError) as ctx:
            self.client.request(
                "POST",
                f"/v1/project-runs/{project_run_id}/partial-reexecute",
                {"from_node": "b", "cascade": False, "instruction_addendum": "x" * 4001},
            )
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")


class DaemonProjectListQueryRouteTests(unittest.TestCase):
    """Regression for query-parameter parsing on /v1/tasks and /v1/projects."""

    @staticmethod
    def _free_port():
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def setUp(self):
        from relay.config import Config
        from relay.daemon import RelayDaemon

        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.config = Config(self.home)
        self.config.init()
        self.config.set("daemon_port", self._free_port())
        self.daemon = RelayDaemon(self.config)
        self.thread = threading.Thread(target=self.daemon.serve, daemon=True)
        self.thread.start()
        from relay.rpc import RPCClient

        self.client = RPCClient(self.config)
        self.assertTrue(self.client.wait_until_healthy(5.0))
        self.engine = self.daemon.engine

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except RelayError:
                pass
            self.thread.join(timeout=5)
        self.temp.cleanup()

    def _seed(self, count_tasks=3, count_projects=2):
        task_ids = []
        for i in range(count_tasks):
            created = self.client.request(
                "POST",
                "/v1/tasks",
                {
                    "name": f"SpecTask-{i}",
                    "instructions": f"work {i}",
                    "worker": "auto",
                    "profile": "web-research",
                    "result_format": "json",
                },
            )
            task_ids.append(created["task"]["task_id"])
        project_ids = []
        for i in range(count_projects):
            project = self.client.request(
                "POST",
                "/v1/projects",
                {
                    "name": f"Project-{i}",
                    "nodes": [{"node_id": "n1", "task_id": task_ids[0]}]
                    if task_ids
                    else [{"node_id": "n1", "task_id": "noop"}],
                    "connections": [],
                    "output_selection": [],
                },
            )
            project_ids.append(project["project"]["project_id"])

    def test_task_list_with_name_filter_returns_matching(self):
        self._seed()
        named = self.client.request("GET", "/v1/tasks?name=SpecTask-1")
        self.assertEqual([t["name"] for t in named["tasks"]], ["SpecTask-1"])
        empty = self.client.request("GET", "/v1/tasks?name=NoSuchTask")
        self.assertEqual(empty["tasks"], [])

    def test_task_list_with_limit_caps_results(self):
        self._seed(count_tasks=4)
        limited = self.client.request("GET", "/v1/tasks?limit=2")
        self.assertEqual(len(limited["tasks"]), 2)

    def test_task_list_with_invalid_limit_returns_400(self):
        with self.assertRaises(RelayError) as ctx:
            self.client.request("GET", "/v1/tasks?limit=oops")
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_project_list_with_name_filter_returns_matching(self):
        self._seed()
        named = self.client.request("GET", "/v1/projects?name=Project-0")
        self.assertEqual([p["name"] for p in named["projects"]], ["Project-0"])
        empty = self.client.request("GET", "/v1/projects?name=NoSuchProject")
        self.assertEqual(empty["projects"], [])

    def test_project_list_with_limit_caps_results(self):
        self._seed(count_projects=4)
        limited = self.client.request("GET", "/v1/projects?limit=2")
        self.assertEqual(len(limited["projects"]), 2)

    def test_project_runs_with_limit_caps_results(self):
        from relay.models import TaskSpec

        ta = self.engine.create_task(TaskSpec(name="TA", instructions="a"))
        project = self.engine.project_service.create_project(
            {
                "name": "P",
                "nodes": [{"node_id": "a", "task_id": ta["task_id"]}],
                "connections": [],
                "output_selection": [],
            }
        )
        for _ in range(3):
            self.client.request("POST", f"/v1/projects/{project['project_id']}/run", {})
        limited = self.client.request("GET", f"/v1/projects/{project['project_id']}/runs?limit=1")
        self.assertEqual(len(limited["project_runs"]), 1)


if __name__ == "__main__":
    unittest.main()
