from __future__ import annotations

from pathlib import Path

from .antigravity import AntigravityAdapter
from .base import Adapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .generic import BUILTIN_WORKERS, GenericCLIAdapter


def get_adapter(name: str, worker_config: dict, spec_root: Path) -> Adapter:
    if name == "claude":
        return ClaudeAdapter(worker_config, spec_root)
    if name == "codex":
        return CodexAdapter(worker_config, spec_root)
    if name == "antigravity":
        return AntigravityAdapter(worker_config, spec_root)
    return GenericCLIAdapter(worker_config, spec_root, name=name)


__all__ = [
    "BUILTIN_WORKERS",
    "GenericCLIAdapter",
    "get_adapter",
    "Adapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "AntigravityAdapter",
]
