from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .agent_apps import AgentAppStore
from .config import Config

BUILTIN_AGENT_IDS = ("claude", "codex", "antigravity")


class AgentRegistry:
    """Compatibility registry for the existing built-in and configured agents."""

    def __init__(self, config: Config, spec_root: Path):
        self.config = config
        self.spec_root = spec_root
        self.agent_apps = AgentAppStore(config)
        self.agent_apps.import_legacy()

    def _agent_ids(self) -> list[str]:
        configured = list(self.config.get("workers", {}).keys())
        manifests = [item["agent_id"] for item in self.agent_apps.list() if item.get("agent_id")]
        return list(dict.fromkeys([*BUILTIN_AGENT_IDS, *configured, *manifests]))

    def get_definition(self, agent_id: str) -> dict[str, Any]:
        manifest = self.agent_apps.get(agent_id) if agent_id not in BUILTIN_AGENT_IDS else None
        if manifest:
            return {
                "agent_id": agent_id,
                "display_name": str(manifest.get("display_name") or agent_id.capitalize()),
                "description": str(manifest.get("description") or ""),
                "enabled": bool(manifest.get("enabled", False)),
                "builtin": False,
                "command": manifest.get("executable"),
                "default_model": manifest.get("default_model"),
                "status": str(manifest.get("status") or ("ready" if manifest.get("enabled") else "disabled")),
                "manifest_hash": self.agent_apps.definition_hash(manifest),
            }
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
            "status": "ready" if config.get("enabled") else "disabled",
        }

    def list_agents(self) -> list[dict[str, Any]]:
        return [self.get_definition(agent_id) for agent_id in self._agent_ids()]

    def list_enabled_agents(self) -> list[dict[str, Any]]:
        return [agent for agent in self.list_agents() if agent["enabled"]]

    def list_agent_ids(self) -> list[str]:
        return self._agent_ids()

    def get_worker_config(self, agent_id: str) -> dict[str, Any]:
        self.get_definition(agent_id)
        if agent_id not in BUILTIN_AGENT_IDS:
            manifest = self.agent_apps.get(agent_id)
            if manifest:
                value = dict(manifest)
                value["_definition_hash"] = self.agent_apps.definition_hash(manifest)
                value["require_deep_doctor"] = True
                return value
        return self.config.worker(agent_id)

    def get_adapter(self, agent_id: str):
        self.get_definition(agent_id)
        if agent_id not in BUILTIN_AGENT_IDS:
            manifest = self.agent_apps.get(agent_id)
            if manifest:
                manifest = dict(manifest)
                manifest["_definition_hash"] = self.agent_apps.definition_hash(manifest)
                manifest["require_deep_doctor"] = True
                return get_adapter(agent_id, manifest, self.spec_root)
        return get_adapter(agent_id, self.config.worker(agent_id), self.spec_root)
