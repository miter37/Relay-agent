"""Relay's application icon: a hand-authored vector mark, not an OS default.

Same rendering approach as :mod:`design_icons` (QSvgRenderer -> QImage at each
requested pixel size, cached), but this asset is full-color/gradient rather
than a single-tone stroke glyph, so it lives in its own module instead of
``ICON_PATHS``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# A monoline "R" monogram on a rounded-square badge: a vertical stem, a
# rounded bowl, and a diagonal leg, drawn as three thick rounded strokes so it
# stays legible down to a 16px taskbar icon without depending on any
# installed font.
_APP_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="badge" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#6FA3FF"/>
      <stop offset="1" stop-color="#2F5FD6"/>
    </linearGradient>
  </defs>
  <rect x="3" y="3" width="58" height="58" rx="16" fill="url(#badge)"/>
  <g fill="none" stroke="#FFFFFF" stroke-width="7" stroke-linecap="round" stroke-linejoin="round">
    <path d="M22 46 L22 18"/>
    <path d="M22 18 H33 A7 7 0 0 1 33 32 H22"/>
    <path d="M27 32 L40 46"/>
  </g>
</svg>
""".strip()

_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
_APP_ICON_CACHE: QIcon | None = None


def _render_app_pixmap(size: int) -> QPixmap:
    renderer = QSvgRenderer(_APP_ICON_SVG.encode("utf-8"))
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    return QPixmap.fromImage(image)


def app_icon() -> QIcon:
    """Return Relay's window/taskbar icon with pixmaps at every common size."""
    global _APP_ICON_CACHE
    if _APP_ICON_CACHE is not None:
        return _APP_ICON_CACHE
    result = QIcon()
    for size in _ICON_SIZES:
        result.addPixmap(_render_app_pixmap(size))
    _APP_ICON_CACHE = result
    return result
