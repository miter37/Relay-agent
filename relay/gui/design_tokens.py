"""Semantic visual tokens for Relay's dark operations-console theme.

Widgets use semantic names rather than literal colors so a future light theme
can replace this module without rewriting every screen.
"""

from __future__ import annotations

from dataclasses import dataclass

from .design_typography import TYPE_SCALE

COLORS: dict[str, str] = {
    # Surfaces (4-tier graphite). Canvas is a step darker than chrome so the
    # work surface reads as a desk, not one flat slab.
    "bg.canvas": "#101010",
    "bg.sidebar": "#161616",
    "bg.topbar": "#161616",
    "bg.surface": "#1C1C1C",
    "bg.surfaceRaised": "#262626",
    "bg.input": "#181818",
    # Interactive surfaces
    "bg.hover": "#2C2C2C",
    "bg.pressed": "#333333",
    "bg.selected": "#243044",
    # Borders
    "border.subtle": "#2C2C2C",
    "border.strong": "#3F3F3F",
    "border.focus": "#7AA2F7",
    # Text
    "text.primary": "#F2F2F2",
    "text.secondary": "#B4B4B4",
    "text.muted": "#9E9E9E",
    # Accent (focus, links, progress). Interactive chrome stays cool.
    "accent.primary": "#4C8DFF",
    "accent.onPrimary": "#0B0B0B",
    # Brand tick for "you are here" in the shell. Warm copper, never a fill.
    # Distinct from accent.relay so Orchestrator moments stay reserved.
    "accent.brand": "#C9956B",
    # Reserved for Orchestrator-authored moments only (live repair, hand-off
    # narration) - never used for ordinary interactive/selection chrome, so it
    # keeps meaning "the Orchestrator did something here" wherever it appears.
    "accent.relay": "#F0A857",
    # Primary action button (neutral bright, Codex/Linear style)
    "action.primaryBg": "#EDEDED",
    "action.primaryFg": "#101010",
    # State
    "state.success": "#5BD48A",
    "state.warning": "#E3B341",
    "state.danger": "#F07A75",
    "state.info": "#79A9FF",
}

SPACING = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "ml": 14, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"badge": 4, "control": 5, "panel": 8}

# controlHeight/rowHeight below used to be hand-picked pixel constants chosen by
# eye against the body type size - as fonts render slightly differently per
# platform/DPI, a box sized independently of the text it holds drifts out of
# alignment with that text. Deriving them from TYPE_SCALE["body"]'s own declared
# size keeps them provably in sync with it instead. This is a static formula
# (not a live QFontMetrics query) so design_tokens stays importable before any
# QApplication exists, exactly like before - only the arithmetic changed, not
# the import-time safety.
_BODY_LINE_HEIGHT = round(TYPE_SCALE["body"].size * 1.4)

METRICS = {
    "controlHeight": _BODY_LINE_HEIGHT + 2 * (SPACING["xs"] + 1),
    "iconButton": 28,
    "iconSize": 16,
    "navIconSize": 18,
    "rowHeight": _BODY_LINE_HEIGHT + 2 * SPACING["xs"],
    "rowPadding": SPACING["xs"] + 1,
    "topBarHeight": 44,
    "navRowHeight": 36,
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
    "needs_review": StatusPresentation("needs_review", "Needs review", "state.warning"),
    # ``awaiting_review`` is the historical run-status spelling. Keep it as
    # an alias so status badges remain useful while API payloads migrate to
    # the workflow-oriented ``needs_review`` vocabulary.
    "awaiting_review": StatusPresentation("needs_review", "Needs review", "state.warning"),
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
