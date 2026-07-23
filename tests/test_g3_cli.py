from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ScheduleCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "relay-home"
        os.environ["RELAY_HOME"] = str(self.home)

    def tearDown(self):
        os.environ.pop("RELAY_HOME", None)
        self.tmp.cleanup()

    def test_parser_accepts_repeated_rule_and_policy_options(self):
        from relay.cli import build_parser

        args = build_parser().parse_args(
            [
                "schedule",
                "create",
                "--from-job",
                "job-1",
                "--name",
                "Morning report",
                "--type",
                "weekly",
                "--time",
                "09:00",
                "--time",
                "18:30",
                "--weekday",
                "1",
                "--weekday",
                "5",
                "--timezone",
                "Asia/Seoul",
                "--overlap-policy",
                "queue",
                "--missed-policy",
                "catch_up",
                "--missed-grace-seconds",
                "3600",
                "--retention-mode",
                "latest_runs",
                "--retention-value",
                "7",
                "--start",
                "2026-07-24T00:00:00+00:00",
                "--end",
                "2026-08-24T00:00:00+00:00",
                "--output-root",
                "/tmp/relay-output",
                "--machine",
            ]
        )

        self.assertEqual(args.command, "schedule")
        self.assertEqual(args.schedule_command, "create")
        self.assertEqual(args.times, ["09:00", "18:30"])
        self.assertEqual(args.weekdays, [1, 5])
        self.assertEqual(args.retention_mode, "latest_runs")
        self.assertTrue(args.machine)

    def test_preview_posts_canonical_rule_and_emits_machine_json(self):
        from relay.cli import main

        client = Mock()
        client.request.return_value = {"ok": True, "occurrences": []}
        output = io.StringIO()
        with patch("relay.cli._ensure_daemon", return_value=client), contextlib.redirect_stdout(output):
            code = main(
                [
                    "schedule",
                    "preview",
                    "--type",
                    "daily",
                    "--time",
                    "09:00",
                    "--timezone",
                    "Asia/Seoul",
                    "--limit",
                    "3",
                    "--after-utc",
                    "2026-07-23T00:00:00+00:00",
                    "--machine",
                ]
            )

        self.assertEqual(code, 0)
        client.request.assert_called_once_with(
            "POST",
            "/v1/schedules/preview",
            {
                "rule": {"type": "daily", "times": ["09:00"], "timezone": "Asia/Seoul"},
                "limit": 3,
                "after_utc": "2026-07-23T00:00:00+00:00",
            },
        )
        self.assertIn('"occurrences":[]', output.getvalue())

    def test_create_and_lifecycle_commands_use_schedule_routes(self):
        from relay.cli import main

        client = Mock()
        client.request.return_value = {"ok": True, "schedule": {"schedule_id": "sch-1"}}
        with patch("relay.cli._ensure_daemon", return_value=client):
            self.assertEqual(
                main(
                    [
                        "schedule",
                        "create",
                        "--from-job",
                        "job-1",
                        "--name",
                        "Daily",
                        "--type",
                        "daily",
                        "--time",
                        "09:00",
                        "--timezone",
                        "UTC",
                        "--retention-mode",
                        "days",
                        "--retention-value",
                        "30",
                        "--machine",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(["schedule", "pause", "sch-1", "--machine"]),
                0,
            )
            self.assertEqual(
                main(["schedule", "runs", "sch-1", "--machine"]),
                0,
            )

        calls = client.request.call_args_list
        self.assertEqual(calls[0].args[:2], ("POST", "/v1/schedules/from-job/job-1"))
        self.assertEqual(
            calls[0].args[2]["retention"],
            {"mode": "days", "value": 30},
        )
        self.assertEqual(calls[1].args[:2], ("POST", "/v1/schedules/sch-1/pause"))
        self.assertEqual(calls[2].args[:2], ("GET", "/v1/schedules/sch-1/runs"))


if __name__ == "__main__":
    unittest.main()
