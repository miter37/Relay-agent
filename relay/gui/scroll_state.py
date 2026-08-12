"""Small helpers for keeping a user's place while a view is refreshed.

Relay refreshes several catalog and detail widgets from polling responses.  Qt
normally resets an item's scroll bar when its contents are replaced, which is
particularly disruptive when the response arrives while somebody is reading
lower down in a panel.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import QTimer

ScrollState = tuple[int, int]


def capture_scroll(widget) -> ScrollState | None:
    """Return the current vertical/horizontal positions for a scrollable widget."""

    try:
        vertical = widget.verticalScrollBar()
        horizontal = widget.horizontalScrollBar()
        return vertical.value(), horizontal.value()
    except (AttributeError, RuntimeError):
        return None


def restore_scroll(widget, state: ScrollState | None) -> None:
    """Restore a captured position, clamped to the widget's current content size."""

    if state is None:
        return
    try:
        vertical = widget.verticalScrollBar()
        horizontal = widget.horizontalScrollBar()
        vertical.setValue(min(state[0], vertical.maximum()))
        horizontal.setValue(min(state[1], horizontal.maximum()))
    except (AttributeError, RuntimeError):
        # A queued refresh may outlive a tab/widget being closed.
        return


@contextmanager
def preserve_scroll(widget) -> Iterator[None]:
    """Preserve a scroll position across a synchronous content replacement.

    The queued restore handles layouts whose new document size is calculated
    after the setter returns (notably QTextBrowser and QScrollArea).
    """

    state = capture_scroll(widget)
    yield
    restore_scroll(widget, state)
    if state is not None:
        QTimer.singleShot(0, lambda: restore_scroll(widget, state))


def set_html(widget, content: str) -> None:
    with preserve_scroll(widget):
        widget.setHtml(content)


def set_markdown(widget, content: str) -> None:
    with preserve_scroll(widget):
        widget.document().setMarkdown(content)


def set_plain_text(widget, content: str) -> None:
    with preserve_scroll(widget):
        widget.setPlainText(content)
