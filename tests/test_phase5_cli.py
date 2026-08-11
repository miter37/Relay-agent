from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from relay.cli import build_parser
from relay.config import Config
from relay.daemon import RelayDaemon
from relay.errors import RelayError
from relay.rpc import RPCClient


class RoutineCLITests(unittest.TestCase):
    def test_routine_create_parses_args(self):
        ns = build_parser().parse_args(
            [
                "routine",
                "create",
                "--name",
                "Daily HBM",
                "--target-type",
                "task",
                "--target-id",
                "T-1",
                "--type",
                "daily",
                "--time",
                "09:00",
                "--timezone",
                "Asia/Seoul",
                "--overlap",
                "skip",
                "--missed",
                "skip",
            ]
        )
        self.assertEqual(ns.command, "routine")
        self.assertEqual(ns.routine_command, "create")
        self.assertEqual(ns.name, "Daily HBM")
        self.assertEqual(ns.target_type, "task")
        self.assertEqual(ns.target_id, "T-1")
        self.assertEqual(ns.type, "daily")
        self.assertEqual(ns.time, ["09:00"])
        self.assertEqual(ns.timezone, "Asia/Seoul")
        self.assertEqual(ns.overlap, "skip")
        self.assertEqual(ns.missed, "skip")

    def test_routine_subcommands(self):
        for subcmd, args in [
            ("list", ["routine", "list", "--name", "HBM", "--machine"]),
            ("show", ["routine", "show", "r-1"]),
            ("update", ["routine", "update", "r-1", "--overlap", "queue"]),
            ("delete", ["routine", "delete", "r-1"]),
            ("run-now", ["routine", "run-now", "r-1"]),
            ("runs", ["routine", "runs", "r-1", "--limit", "10"]),
            ("receipt", ["routine", "receipt", "r-1"]),
        ]:
            with self.subTest(cmd=subcmd):
                ns = build_parser().parse_args(args)
                self.assertEqual(ns.routine_command, subcmd)
        ns = build_parser().parse_args(
            ["routine", "preview", "--type", "weekly", "--time", "08:00", "--weekday", "1", "--timezone", "UTC"]
        )
        self.assertEqual(ns.routine_command, "preview")
        self.assertEqual(ns.weekday, [1])


class DaemonRoutineListQueryRouteTests(unittest.TestCase):
    """Regression for query-parameter parsing on /v1/routines."""

    @staticmethod
    def _free_port():
        import socket

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
        self.engine = self.daemon.engine

    def tearDown(self):
        if self.thread.is_alive():
            try:
                self.client.request("POST", "/shutdown")
            except RelayError:
                pass
            self.thread.join(timeout=5)
        self.temp.cleanup()

    def _seed_routine(self, name="Daily"):
        from relay.models import TaskSpec

        task = self.engine.create_task(TaskSpec(name=f"DemoTask-{name}", instructions="do work"))
        routine = self.engine.routine_service.create_routine(
            {
                "name": name,
                "target_type": "task",
                "target_id": task["task_id"],
                "rule": {"type": "daily", "times": ["09:00"], "timezone": "UTC"},
            }
        )
        return {"ok": True, "routine": routine}

    def test_routine_list_with_name_filter_returns_matching(self):
        self._seed_routine("Daily-Marketing")
        self._seed_routine("Weekly-Dev")
        named = self.client.request("GET", "/v1/routines?name=Daily")
        self.assertEqual([r["name"] for r in named["routines"]], ["Daily-Marketing"])

    def test_routine_list_with_limit_caps_results(self):
        for i in range(4):
            self._seed_routine(f"Routine-{i}")
        limited = self.client.request("GET", "/v1/routines?limit=2")
        self.assertEqual(len(limited["routines"]), 2)

    def test_routine_list_with_invalid_limit_returns_400(self):
        with self.assertRaises(RelayError) as ctx:
            self.client.request("GET", "/v1/routines?limit=oops")
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_routine_runs_route_accepts_limit_param(self):
        routine = self._seed_routine("R")
        rid = routine["routine"]["routine_id"]
        # No seeded runs; just verify the limit param is accepted without error.
        ok = self.client.request("GET", f"/v1/routines/{rid}/runs?limit=2")
        self.assertTrue(ok.get("ok"))
        self.assertEqual(ok.get("routine_id"), rid)
        self.assertIsInstance(ok.get("runs"), list)

    def test_routine_preview_and_receipt_routes(self):
        routine = self._seed_routine("Preview")
        rid = routine["routine"]["routine_id"]
        preview = self.client.request(
            "POST",
            "/v1/routines/preview",
            {"rule": {"type": "daily", "times": ["09:00"]}, "timezone": "UTC", "limit": 2},
        )
        self.assertTrue(preview.get("ok"))
        self.assertLessEqual(len(preview.get("items", [])), 2)
        receipt = self.client.request("GET", f"/v1/routines/{rid}/receipt")
        self.assertTrue(receipt.get("ok"))
        self.assertEqual(receipt["receipt"]["routine"]["routine_id"], rid)


if __name__ == "__main__":
    unittest.main()
