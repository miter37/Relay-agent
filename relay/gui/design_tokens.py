"""Semantic visual tokens for Relay's dark operations-console theme.

Widgets use semantic names rather than literal colors so a future light theme
can replace this module without rewriting every screen.
"""

from __future__ import annotations

from dataclasses import dataclass

COLORS: dict[str, str] = {
    "bg.canvas": "#0B1220",
    "bg.sidebar": "#111827",
    "bg.topbar": "#101A2D",
    "bg.surface": "#172235",
    "bg.surfaceRaised": "#202D42",
    "bg.input": "#0F1A2B",
    "border.subtle": "#2A3850",
    "border.focus": "#38BDF8",
    "text.primary": "#E5EDF8",
    "text.secondary": "#A9B8CC",
    "text.muted": "#71819A",
    "accent.primary": "#3B9CFF",
    "accent.cyan": "#35C8F2",
    "state.success": "#58D68D",
    "state.warning": "#F4C95D",
    "state.danger": "#F07178",
    "state.info": "#7AA2F7",
}

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"control": 6, "panel": 10}


@dataclass(frozen=True)
class StatusPresentation:
    state: str
    label: str
    color_token: str


_STATUS_PRESENTATIONS = {
    "running": StatusPresentation("running", "Running", "state.info"),
    "processing": StatusPresentation("running", "Processing", "state.info"),
    "queued": StatusPresentation("queued", "Queued", "state.warning"),
    "completed": StatusPresentation("completed", "Completed", "state.success"),
    "success": StatusPresentation("completed", "Completed", "state.success"),
    "partial": StatusPresentation("partial", "Partial", "state.warning"),
    "needs_approval": StatusPresentation("needs_approval", "Needs approval", "state.warning"),
    "failed": StatusPresentation("failed", "Failed", "state.danger"),
    "cancelled": StatusPresentation("cancelled", "Cancelled", "text.muted"),
    "unavailable": StatusPresentation("unavailable", "Unavailable", "text.muted"),
}


def status_presentation(value: object) -> StatusPresentation:
    """Return Relay's stable visual language for any daemon status value."""
    normalized = str(value or "unavailable").strip().casefold().replace("-", "_").replace(" ", "_")
    return _STATUS_PRESENTATIONS.get(normalized, _STATUS_PRESENTATIONS["unavailable"])
