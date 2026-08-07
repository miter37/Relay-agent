"""Relay's typography scale. Widget fonts are decided only here.

Qt Style Sheets do not support ``letter-spacing`` or ``line-height``, and any
``font-size``/``font-weight`` set via QSS silently overrides a widget's
``QFont``. To keep the two systems from fighting, QSS handles color/background/
border/radius/padding only; every widget's font comes from :func:`apply_type`.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget

UI_FAMILIES = [
    "Segoe UI Variable Text",
    "Segoe UI",
    "SF Pro Text",
    "Inter",
    "Noto Sans",
    "DejaVu Sans",
    "Malgun Gothic",
    "Apple SD Gothic Neo",
    "Noto Sans KR",
]

MONO_FAMILIES = [
    "Cascadia Mono",
    "Consolas",
    "SF Mono",
    "Menlo",
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "D2Coding",
    "Malgun Gothic",
]


@dataclass(frozen=True)
class TypeRole:
    size: int
    weight: int
    tracking: float
    mono: bool = False
    uppercase: bool = False


TYPE_SCALE: dict[str, TypeRole] = {
    "title.page": TypeRole(20, QFont.DemiBold, -0.2),
    "title.detail": TypeRole(16, QFont.DemiBold, -0.1),
    "title.section": TypeRole(13, QFont.DemiBold, 0.0),
    "body": TypeRole(13, QFont.Normal, 0.0),
    "body.strong": TypeRole(13, QFont.Medium, 0.0),
    "caption": TypeRole(12, QFont.Normal, 0.0),
    "overline": TypeRole(11, QFont.DemiBold, 0.6, uppercase=True),
    "mono": TypeRole(12, QFont.Normal, 0.0, mono=True),
}

_FONT_CACHE: dict[str, QFont] = {}


def _build_font(role: TypeRole) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES if role.mono else UI_FAMILIES)
    font.setPixelSize(role.size)
    font.setWeight(role.weight)
    font.setLetterSpacing(QFont.AbsoluteSpacing, role.tracking)
    if role.uppercase:
        font.setCapitalization(QFont.AllUppercase)
    return font


def font_for(role: str) -> QFont:
    """Return a cached :class:`QFont` for a :data:`TYPE_SCALE` role name."""
    cached = _FONT_CACHE.get(role)
    if cached is not None:
        return QFont(cached)
    spec = TYPE_SCALE[role]
    font = _build_font(spec)
    _FONT_CACHE[role] = font
    return QFont(font)


def apply_type(widget: QWidget, role: str) -> None:
    """Set ``widget``'s font to the given type-scale role."""
    widget.setFont(font_for(role))


def application_font() -> QFont:
    """The default font for ``QApplication.setFont()`` (the ``body`` role)."""
    return font_for("body")
