"""Human-oriented JSON rendering for QTextBrowser detail surfaces.

Raw JSON is useful for diagnostics, but it is a poor default for reviewing a
Task/Project result.  This module renders objects as property tables and
homogeneous arrays of objects as data tables.  Nested values remain visible as
an indented table inside their parent cell, so the representation stays
faithful without forcing the reader through a wall of braces and quotes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from .design_html import td, td_html, th_row
from .design_tokens import COLORS, SPACING

_MAX_DEPTH = 8
_MAX_ROWS = 200
_MAX_COLUMNS = 24
_CELL_STYLE = f"padding:{SPACING['xxs']}px {SPACING['sm']}px; vertical-align:top;"
_MUTED_STYLE = f"color:{COLORS['text.muted']};"
_TYPE_STYLE = f"color:{COLORS['text.muted']}; white-space:nowrap;"
_NESTED_TABLE_STYLE = (
    f"border:1px solid {COLORS['border.subtle']}; border-collapse:collapse; "
    f"margin:{SPACING['xs']}px 0; width:100%;"
)


def _type_label(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    return "string"


def _primitive_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _summary(value: Any) -> str:
    if isinstance(value, Mapping):
        return "Object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "Array"
    return _primitive_text(value)


def _type_html(value: Any) -> str:
    return f'<span style="{_TYPE_STYLE}">{_type_label(value)}</span>'


def _cell_html(value: Any, depth: int) -> str:
    if depth >= _MAX_DEPTH:
        return f'<span style="{_MUTED_STYLE}">Nested value hidden at depth limit</span>'
    if isinstance(value, Mapping):
        return _object_table(value, depth + 1)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _array_table(value, depth + 1)
    return escape(_primitive_text(value))


def _object_table(value: Mapping[Any, Any], depth: int) -> str:
    if not value:
        return f'<span style="{_MUTED_STYLE}">Empty object</span>'
    rows: list[str] = []
    items = list(value.items())[:_MAX_ROWS]
    for key, child in items:
        rendered = _cell_html(child, depth)
        field_html = f"<b>{escape(str(key))}</b>"
        rows.append(
            f"<tr>{td_html(field_html)}{td_html(rendered)}"
            f"{td_html(_type_html(child))}</tr>"
        )
    if len(value) > _MAX_ROWS:
        rows.append(
            f'<tr><td colspan="3" style="{_CELL_STYLE}{_MUTED_STYLE}">'
            f"{len(value) - _MAX_ROWS} more fields hidden</td></tr>"
        )
    return f'<table style="{_NESTED_TABLE_STYLE}">{th_row(("Field", "Value", "Type"))}{"".join(rows)}</table>'


def _array_of_objects(value: Sequence[Any]) -> bool:
    return bool(value) and all(isinstance(item, Mapping) for item in value)


def _array_table(value: Sequence[Any], depth: int) -> str:
    if not value:
        return f'<span style="{_MUTED_STYLE}">Empty array</span>'

    if _array_of_objects(value):
        mappings = [item for item in value if isinstance(item, Mapping)]
        keys: list[str] = []
        for item in mappings:
            for key in item:
                key_text = str(key)
                if key_text not in keys:
                    keys.append(key_text)
                if len(keys) >= _MAX_COLUMNS:
                    break
            if len(keys) >= _MAX_COLUMNS:
                break
        header = th_row(("#", *keys))
        rows: list[str] = []
        for index, item in enumerate(mappings[:_MAX_ROWS], start=1):
            cells = [td(index)]
            for key in keys:
                child = item.get(key)
                cells.append(td_html(_cell_html(child, depth)))
            rows.append(f"<tr>{''.join(cells)}</tr>")
        if len(mappings) > _MAX_ROWS:
            rows.append(
                f'<tr><td colspan="{len(keys) + 1}" style="{_CELL_STYLE}{_MUTED_STYLE}">'
                f"{len(mappings) - _MAX_ROWS} more rows hidden</td></tr>"
            )
        return f'<table style="{_NESTED_TABLE_STYLE}">{header}{"".join(rows)}</table>'

    rows = []
    for index, child in enumerate(value[:_MAX_ROWS]):
        rows.append(
            f"<tr>{td(index)}{td_html(_cell_html(child, depth))}"
            f"{td_html(_type_html(child))}</tr>"
        )
    if len(value) > _MAX_ROWS:
        rows.append(
            f'<tr><td colspan="3" style="{_CELL_STYLE}{_MUTED_STYLE}">'
            f"{len(value) - _MAX_ROWS} more items hidden</td></tr>"
        )
    return f'<table style="{_NESTED_TABLE_STYLE}">{th_row(("#", "Value", "Type"))}{"".join(rows)}</table>'


def render_json_html(value: Any) -> str:
    """Render JSON-compatible data as a readable table-based HTML fragment."""

    if isinstance(value, Mapping):
        return _object_table(value, 0)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _array_table(value, 0)
    return (
        f'<table style="{_NESTED_TABLE_STYLE}">{th_row(("Value", "Type"))}'
        f"<tr>{td_html(_cell_html(value, 0))}{td_html(_type_html(value))}</tr></table>"
    )


def _is_json_container(value: Any) -> bool:
    return isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
    )


def _report_value_html(value: Any) -> str:
    return escape(_primitive_text(value)).replace("\n", "<br>")


def _report_row_html(indent: int, label: str, value: Any = None, *, has_value: bool = False) -> str:
    indentation = "&nbsp;" * (indent * 4)
    label_html = escape(label)
    value_html = f"<span>{_report_value_html(value)}</span>" if has_value else ""
    separator = "  " if has_value else ""
    return (
        f'<div style="font-size:13px; padding-left:{indent * 18}px; margin:0 0 3px 0; '
        f'white-space:normal; word-wrap:break-word;">'
        f'<span style="font-size:13px; font-weight:600;">{indentation}{label_html}</span>'
        f'<span style="font-size:13px; font-weight:normal;">{separator}{value_html}</span></div>'
    )


def _append_report_mapping(value: Mapping[Any, Any], level: int, lines: list[str]) -> None:
    items = list(value.items())[:_MAX_ROWS]
    for key, child in items:
        if _is_json_container(child):
            lines.append(_report_row_html(level, str(key)))
            _append_report_container(child, level + 1, lines)
        else:
            lines.append(_report_row_html(level, str(key), child, has_value=True))
    if len(value) > _MAX_ROWS:
        lines.append(_report_row_html(level, "…", f"{len(value) - _MAX_ROWS} more fields hidden", has_value=True))


def _append_report_array(value: Sequence[Any], level: int, lines: list[str]) -> None:
    items = list(value)[:_MAX_ROWS]
    if not items:
        lines.append(_report_row_html(level, "(empty)"))
        return
    for index, child in enumerate(items):
        if _is_json_container(child):
            lines.append(_report_row_html(level, f"[{index}]"))
            _append_report_container(child, level + 1, lines)
        else:
            lines.append(_report_row_html(level, f"[{index}]", child, has_value=True))
    if len(value) > _MAX_ROWS:
        lines.append(_report_row_html(level, "…", f"{len(value) - _MAX_ROWS} more items hidden", has_value=True))


def _append_report_container(value: Any, level: int, lines: list[str]) -> None:
    if isinstance(value, Mapping):
        _append_report_mapping(value, level, lines)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        _append_report_array(value, level, lines)


def render_json_report_html(value: Any) -> str:
    """Render JSON as a same-size, indented report text surface.

    This intentionally does not use Markdown headings.  Every line stays at
    the same visual size; bold is reserved for field names and array indices,
    while indentation and line breaks communicate hierarchy.
    """

    lines: list[str] = []
    if isinstance(value, Mapping):
        _append_report_mapping(value, 0, lines)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        _append_report_array(value, 0, lines)
    else:
        lines.append(_report_row_html(0, "Value", value, has_value=True))
    return "".join(lines) or _report_row_html(0, "(empty)")
