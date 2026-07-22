from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DiscoveredModel:
    id: str
    display_name: str
    selectable_name: str
    availability: str
    is_default: bool = False
    hidden: bool = False
    reasoning_efforts: list[str] = field(default_factory=list)
    default_reasoning_effort: str | None = None
    verification_method: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "selectable_name": self.selectable_name,
            "availability": self.availability,
            "is_default": self.is_default,
            "hidden": self.hidden,
            "reasoning_efforts": self.reasoning_efforts,
            "default_reasoning_effort": self.default_reasoning_effort,
            "verification_method": self.verification_method,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ModelCatalog:
    worker: str
    cli_version: str | None
    status: str
    source: str
    account_scoped: bool
    authoritative: bool
    models: list[DiscoveredModel]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "cli_version": self.cli_version,
            "status": self.status,
            "source": self.source,
            "account_scoped": self.account_scoped,
            "authoritative": self.authoritative,
            "models": [m.to_dict() for m in self.models],
            "warnings": self.warnings,
        }
