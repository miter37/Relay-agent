from __future__ import annotations

import unittest

from relay.cli import _preprocess, build_parser


class G5AgentCliTests(unittest.TestCase):
    def test_agent_app_is_not_rewritten_as_a_run_task(self):
        self.assertEqual(_preprocess(["agent-app", "list"]), ["agent-app", "list"])

    def test_agent_app_lifecycle_commands_parse(self):
        parser = build_parser()

        for command in ("list", "show", "test", "enable", "disable", "delete"):
            args = parser.parse_args(["agent-app", command, *([] if command == "list" else ["opencode"])])
            self.assertEqual(args.command, "agent-app")
            self.assertEqual(args.agent_app_command, command)


if __name__ == "__main__":
    unittest.main()
