from __future__ import annotations

import unittest

from relay.cli import build_parser


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


if __name__ == "__main__":
    unittest.main()
