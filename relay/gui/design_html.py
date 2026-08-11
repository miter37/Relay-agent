"""Shared HTML table cell helpers for the QTextBrowser detail/definition panels.

QTextBrowser's HTML subset renders <table>/<td>/<th> with no default cell
padding at all - every screen that formats a table into a QTextBrowser goes
through these two helpers so that padding is applied once, consistently,
instead of each screen remembering (or forgetting) to add it inline.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape

from .design_tokens import COLORS, SPACING

_CELL_STYLE = f"padding:{SPACING['xxs']}px {SPACING['sm']}px;"
_HEADER_STYLE = f"{_CELL_STYLE}color:{COLORS['text.secondary']};text-align:left;"


def td(value: object) -> str:
    return f'<td style="{_CELL_STYLE}">{escape(str(value))}</td>'


def td_html(html: str) -> str:
    """Like :func:`td`, but for a cell that is already-formed HTML (e.g. a link)."""
    return f'<td style="{_CELL_STYLE}">{html}</td>'


def th_row(labels: Iterable[object]) -> str:
    cells = "".join(f'<th style="{_HEADER_STYLE}">{escape(str(label))}</th>' for label in labels)
    return f"<tr>{cells}</tr>"


def kv_row(key: object, value: object) -> str:
    """A bold-label/value row for key-value detail tables (no header row)."""
    return (
        f'<tr><td style="{_CELL_STYLE}"><b>{escape(str(key))}</b></td>'
        f'<td style="{_CELL_STYLE}">{escape(str(value))}</td></tr>'
    )
