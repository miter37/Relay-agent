from __future__ import annotations

from pathlib import Path

from .antigravity import AntigravityAdapter
from .base import Adapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter


def get_adapter(name: str, worker_config: dict, spec_root: Path) -> Adapter:
    if name == "claude":
        return ClaudeAdapter(worker_config, spec_root)
    if name == "codex":
        return CodexAdapter(worker_config, spec_root)
    if name == "antigravity":
        return AntigravityAdapter(worker_config, spec_root)
    raise ValueError(f"Unsupported worker: {name}")


__all__ = ["get_adapter", "Adapter", "ClaudeAdapter", "CodexAdapter", "AntigravityAdapter"]
