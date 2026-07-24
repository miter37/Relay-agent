from __future__ import annotations

import unittest

from relay.cli import build_parser


class G5AgentCliTests(unittest.TestCase):
    def test_agent_app_lifecycle_commands_parse(self):
        parser = build_parser()

        for command in ("list", "show", "test", "enable", "disable", "delete"):
            args = parser.parse_args(["agent-app", command, *([] if command == "list" else ["opencode"])])
            self.assertEqual(args.command, "agent-app")
            self.assertEqual(args.agent_app_command, command)


if __name__ == "__main__":
    unittest.main()
