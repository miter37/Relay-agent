"""Semantic visual tokens for Relay's dark operations-console theme.

Widgets use semantic names rather than literal colors so a future light theme
can replace this module without rewriting every screen.
"""

from __future__ import annotations

from dataclasses import dataclass

COLORS: dict[str, str] = {
    # Surfaces (4-tier neutral dark)
    "bg.canvas": "#131313",
    "bg.sidebar": "#181818",
    "bg.topbar": "#181818",
    "bg.surface": "#1C1C1C",
    "bg.surfaceRaised": "#242424",
    "bg.input": "#1F1F1F",
    # Interactive surfaces
    "bg.hover": "#2A2A2A",
    "bg.pressed": "#303030",
    "bg.selected": "#26364F",
    # Borders
    "border.subtle": "#2E2E2E",
    "border.strong": "#3D3D3D",
    "border.focus": "#7AA2F7",
    # Text
    "text.primary": "#EDEDED",
    "text.secondary": "#B0B0B0",
    "text.muted": "#999999",
    # Accent (selection, focus, progress, active indicators)
    "accent.primary": "#4C8DFF",
    "accent.onPrimary": "#0B0B0B",
    # Primary action button (neutral bright, Codex/Linear style)
    "action.primaryBg": "#EDEDED",
    "action.primaryFg": "#131313",
    # State
    "state.success": "#5BD48A",
    "state.warning": "#E3B341",
    "state.danger": "#F07A75",
    "state.info": "#79A9FF",
}

SPACING = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"badge": 4, "control": 5, "panel": 8}
METRICS = {
    "controlHeight": 28,
    "iconButton": 28,
    "iconSize": 16,
    "navIconSize": 18,
    "rowHeight": 26,
    "rowPadding": 5,
    "topBarHeight": 48,
    "sidebarWidth": 200,
}


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG relative-luminance contrast ratio for two hex colors."""

    def luminance(value: str) -> float:
        channels = [int(value[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@dataclass(frozen=True)
class StatusPresentation:
    state: str
    label: str
    color_token: str


_STATUS_PRESENTATIONS = {
    "running": StatusPresentation("running", "Running", "state.info"),
    "info": StatusPresentation("info", "Info", "state.info"),
    "processing": StatusPresentation("running", "Processing", "state.info"),
    "queued": StatusPresentation("queued", "Queued", "state.warning"),
    "warning": StatusPresentation("queued", "Warning", "state.warning"),
    "completed": StatusPresentation("completed", "Completed", "state.success"),
    "success": StatusPresentation("completed", "Completed", "state.success"),
    "partial": StatusPresentation("partial", "Partial", "state.warning"),
    "needs_approval": StatusPresentation("needs_approval", "Needs approval", "state.warning"),
    "failed": StatusPresentation("failed", "Failed", "state.danger"),
    "danger": StatusPresentation("failed", "Error", "state.danger"),
    "error": StatusPresentation("failed", "Error", "state.danger"),
    "cancelled": StatusPresentation("cancelled", "Cancelled", "text.muted"),
    "unavailable": StatusPresentation("unavailable", "Unavailable", "text.muted"),
}


def status_presentation(value: object) -> StatusPresentation:
    """Return Relay's stable visual language for any daemon status value."""
    normalized = str(value or "unavailable").strip().casefold().replace("-", "_").replace(" ", "_")
    return _STATUS_PRESENTATIONS.get(normalized, _STATUS_PRESENTATIONS["unavailable"])
