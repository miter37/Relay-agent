from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .config import Config

BUILTIN_AGENT_IDS = ("claude", "codex", "antigravity")


class AgentRegistry:
    """Compatibility registry for the existing built-in and configured agents."""

    def __init__(self, config: Config, spec_root: Path):
        self.config = config
        self.spec_root = spec_root

    def _agent_ids(self) -> list[str]:
        configured = list(self.config.get("workers", {}).keys())
        return list(dict.fromkeys([*BUILTIN_AGENT_IDS, *configured]))

    def get_definition(self, agent_id: str) -> dict[str, Any]:
        config = self.config.worker(agent_id)
        if not config:
            raise KeyError(agent_id)
        return {
            "agent_id": agent_id,
            "display_name": str(config.get("display_name") or agent_id.capitalize()),
            "description": str(config.get("description") or ""),
            "enabled": bool(config.get("enabled", False)),
            "builtin": agent_id in BUILTIN_AGENT_IDS,
            "command": config.get("command"),
            "default_model": config.get("default_model"),
        }

    def list_agents(self) -> list[dict[str, Any]]:
        return [self.get_definition(agent_id) for agent_id in self._agent_ids()]

    def list_enabled_agents(self) -> list[dict[str, Any]]:
        return [agent for agent in self.list_agents() if agent["enabled"]]

    def get_adapter(self, agent_id: str):
        self.get_definition(agent_id)
        return get_adapter(agent_id, self.config.worker(agent_id), self.spec_root)
