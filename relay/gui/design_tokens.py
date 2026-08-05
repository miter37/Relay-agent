"""Semantic visual tokens for Relay's dark operations-console theme.

Widgets use semantic names rather than literal colors so a future light theme
can replace this module without rewriting every screen.
"""

from __future__ import annotations

from dataclasses import dataclass

COLORS: dict[str, str] = {
    "bg.canvas": "#0F172A",
    "bg.sidebar": "#111C2E",
    "bg.topbar": "#162238",
    "bg.surface": "#1B2A40",
    "bg.surfaceRaised": "#263A55",
    "bg.input": "#121F33",
    "border.subtle": "#354A66",
    "border.focus": "#6DD6F7",
    "text.primary": "#F3F7FC",
    "text.secondary": "#C3D0E0",
    "text.muted": "#9AAAC0",
    "accent.primary": "#1769AA",
    "accent.cyan": "#6DD6F7",
    "state.success": "#70E0A0",
    "state.warning": "#FFD166",
    "state.danger": "#FF8A8F",
    "state.info": "#9BB8FF",
}

SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"control": 6, "panel": 10}


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
