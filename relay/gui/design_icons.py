"""Relay's built-in 16px stroke icon set.

No external icon library dependency: each icon is a small hand-authored SVG
body rendered through :mod:`PySide6.QtSvg` and tinted with token colors. Icons
are cached per (name, tone, size, device-pixel-ratio) and rendered at the
correct pixel density so they stay crisp at 100/125/150% DPI scaling.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from .design_tokens import COLORS

_VIEWBOX = 24

# Each body uses {c} as the stroke/fill color placeholder. Elements that
# should be filled instead of stroked set fill="{c}" stroke="none" inline.
ICON_PATHS: dict[str, str] = {
    "refresh": '<path d="M20 11A8 8 0 1 0 18.5 16"/><polyline points="20 4 20 11 13 11"/>',
    "play": '<path d="M7 4l13 8-13 8V4z" fill="{c}" stroke="none"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5" fill="{c}" stroke="none"/>',
    "pause": (
        '<rect x="6" y="4" width="4" height="16" rx="1" fill="{c}" stroke="none"/>'
        '<rect x="14" y="4" width="4" height="16" rx="1" fill="{c}" stroke="none"/>'
    ),
    "rerun": '<path d="M20 11A8 8 0 1 0 18.5 16"/><polyline points="20 4 20 11 13 11"/>',
    "activity": '<polyline points="3 12 8 12 10 6 14 18 16 12 21 12"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "pencil": '<path d="M4 20l1-4.5L16.5 4l3.5 3.5L8.5 19 4 20z"/><line x1="14.5" y1="6" x2="18" y2="9.5"/>',
    "trash": (
        '<polyline points="4 7 20 7"/><path d="M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7"/>'
        '<path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>'
        '<line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>'
    ),
    "copy": '<rect x="9" y="9" width="11" height="11" rx="1.5"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/>',
    "arrow-up": '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="6 11 12 5 18 11"/>',
    "arrow-down": '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="6 13 12 19 18 13"/>',
    "folder-open": (
        '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5V10H4z"/>'
        '<path d="M4 10l-1 8.5A1.5 1.5 0 0 0 4.5 20h15a1.5 1.5 0 0 0 1.5-1.5L21.5 11a1 1 0 0 0-1-1H4"/>'
    ),
    "file-text": '<path d="M7 3h7l4 4v14H7z"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="15" y2="16"/>',
    "external-link": (
        '<path d="M14 4h6v6"/><line x1="20" y1="4" x2="11" y2="13"/>'
        '<path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="8.5"/><polyline points="12 7.5 12 12 15.5 14"/>',
    "calendar": (
        '<rect x="3.5" y="5" width="17" height="16" rx="1.5"/><line x1="3.5" y1="9.5" x2="20.5" y2="9.5"/>'
        '<line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/>'
    ),
    "repeat": (
        '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/>'
        '<path d="M7 22l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>'
    ),
    "dot": '<circle cx="12" cy="12" r="4" fill="{c}" stroke="none"/>',
    "check-circle": '<circle cx="12" cy="12" r="8.5"/><polyline points="8 12.5 11 15.5 16.5 9"/>',
    "alert-triangle": (
        '<path d="M12 3.5l9.5 16.5H2.5z"/><line x1="12" y1="9.5" x2="12" y2="14"/>'
        '<circle cx="12" cy="17" r="0.9" fill="{c}" stroke="none"/>'
    ),
    "x-circle": '<circle cx="12" cy="12" r="8.5"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>',
    "info": (
        '<circle cx="12" cy="12" r="8.5"/><line x1="12" y1="11" x2="12" y2="16"/>'
        '<circle cx="12" cy="7.5" r="0.9" fill="{c}" stroke="none"/>'
    ),
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><line x1="20" y1="20" x2="15.2" y2="15.2"/>',
    "filter": '<path d="M3.5 4.5h17L14 12.5v6l-4 2v-8z"/>',
    "power": '<line x1="12" y1="3.5" x2="12" y2="11"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
    "beaker": (
        '<path d="M9 3h6"/><path d="M10 3v6.5L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9.5V3"/>'
        '<line x1="7" y1="15" x2="17" y2="15"/>'
    ),
    "chevron-down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron-right": '<polyline points="9 6 15 12 9 18"/>',
    "x": '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
    "list": (
        '<line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="18" x2="20" y2="18"/>'
        '<circle cx="4.5" cy="6" r="1" fill="{c}" stroke="none"/>'
        '<circle cx="4.5" cy="12" r="1" fill="{c}" stroke="none"/>'
        '<circle cx="4.5" cy="18" r="1" fill="{c}" stroke="none"/>'
    ),
    "checklist": (
        '<polyline points="3.5 6.5 5 8 8 5"/><line x1="11" y1="6" x2="20.5" y2="6"/>'
        '<polyline points="3.5 13.5 5 15 8 12"/><line x1="11" y1="13" x2="20.5" y2="13"/>'
        '<polyline points="3.5 19.5 5 21 8 18"/><line x1="11" y1="19" x2="20.5" y2="19"/>'
    ),
    "user": '<circle cx="12" cy="8" r="3.5"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>',
    "folder-tree": (
        '<path d="M3.5 4.5h5l1.5 2h10a1 1 0 0 1 1 1v11.5a1 1 0 0 1-1 1h-15.5a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1z"/>'
    ),
    "gear": (
        '<circle cx="12" cy="12" r="3.2"/>'
        '<path d="M12 2.5v2.4M12 19.1v2.4M4.4 6.4l1.7 1.7M17.9 15.9l1.7 1.7'
        'M2.5 12h2.4M19.1 12h2.4M4.4 17.6l1.7-1.7M17.9 8.1l1.7-1.7"/>'
    ),
}

_TONE_COLOR_TOKENS = {
    "default": ("text.secondary", "text.primary", "text.muted"),
    "accent": ("accent.primary", "text.primary", "text.muted"),
    "danger": ("state.danger", "text.primary", "text.muted"),
    "muted": ("text.muted", "text.secondary", "text.muted"),
    # Sits on the light primary-action surface, but a disabled primary button
    # falls back to a dark surface, so the disabled tint must stay legible there.
    "onPrimary": ("action.primaryFg", "action.primaryFg", "text.muted"),
}

_PIXMAP_CACHE: dict[tuple[str, str, int, float], QPixmap] = {}
_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


def _device_pixel_ratio() -> float:
    app = QApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    return float(screen.devicePixelRatio() or 1.0)


def _render_pixmap(name: str, color_hex: str, size: int, dpr: float) -> QPixmap:
    cache_key = (name, color_hex, size, dpr)
    cached = _PIXMAP_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if name not in ICON_PATHS:
        raise KeyError(f"Unknown icon name: {name!r}")
    body = ICON_PATHS[name].format(c=color_hex)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_VIEWBOX} {_VIEWBOX}">'
        f'<g fill="none" stroke="{color_hex}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</g></svg>'
    )
    renderer = QSvgRenderer(svg.encode("utf-8"))
    device_size = max(1, round(size * dpr))
    image = QImage(device_size, device_size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    _PIXMAP_CACHE[cache_key] = pixmap
    return pixmap


def icon(name: str, tone: str = "default") -> QIcon:
    """Return a cached :class:`QIcon` for ``name`` tinted per ``tone``.

    Raises ``KeyError`` for unknown icon names rather than returning a blank
    icon, so a typo fails loudly instead of silently rendering nothing.
    """
    if name not in ICON_PATHS:
        raise KeyError(f"Unknown icon name: {name!r}")
    size = 16
    cache_key = (name, tone, size)
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    dpr = _device_pixel_ratio()
    normal_token, active_token, disabled_token = _TONE_COLOR_TOKENS.get(tone, _TONE_COLOR_TOKENS["default"])
    result = QIcon()
    result.addPixmap(_render_pixmap(name, COLORS[normal_token], size, dpr), QIcon.Normal)
    result.addPixmap(_render_pixmap(name, COLORS[active_token], size, dpr), QIcon.Active)
    result.addPixmap(_render_pixmap(name, COLORS[disabled_token], size, dpr), QIcon.Disabled)
    _ICON_CACHE[cache_key] = result
    return result
