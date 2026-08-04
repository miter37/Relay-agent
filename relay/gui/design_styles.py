"""Application-wide QSS generated from Relay's semantic design tokens."""

from __future__ import annotations

from .design_tokens import COLORS, RADIUS, SPACING


def application_stylesheet() -> str:
    """Return the single dark-theme stylesheet installed by :mod:`gui.app`."""
    color = COLORS
    return f"""
        QApplication, QMainWindow {{
            background: {color["bg.canvas"]}; color: {color["text.primary"]};
            font-size: 13px;
        }}
        QWidget#topBar {{ background: {color["bg.topbar"]}; border-bottom: 1px solid {color["border.subtle"]}; }}
        QWidget#sidebarNav {{ background: {color["bg.sidebar"]}; border-right: 1px solid {color["border.subtle"]}; }}
        QFrame#surface, QFrame#metricCard, QWidget#emptyState {{
            background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["panel"]}px;
        }}
        QLabel#pageTitle {{ color: {color["text.primary"]}; font-size: 22px; font-weight: 600; }}
        QLabel#mutedText {{ color: {color["text.muted"]}; font-size: 11px; }}
        QLabel#errorText {{ color: {color["state.danger"]}; }}
        QPushButton {{
            background: {color["bg.surfaceRaised"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["control"]}px; color: {color["text.primary"]}; padding: {SPACING["sm"]}px {SPACING["md"]}px;
        }}
        QPushButton:hover {{ border-color: {color["accent.cyan"]}; background: {color["bg.surface"]}; }}
        QPushButton:disabled {{ color: {color["text.muted"]}; background: {color["bg.input"]}; }}
        QPushButton#primaryAction {{ background: {color["accent.primary"]}; border-color: {color["accent.primary"]}; color: white; font-weight: 700; }}
        QPushButton#primaryAction:hover {{ background: {color["accent.cyan"]}; border-color: {color["accent.cyan"]}; }}
        QPushButton#dangerAction {{ color: {color["state.danger"]}; }}
        QPushButton#sidebarButton {{ text-align: left; background: transparent; border: 0; border-radius: {RADIUS["control"]}px; }}
        QPushButton#sidebarButton:hover, QPushButton#sidebarButton:checked {{ background: {color["bg.surfaceRaised"]}; color: {color["accent.cyan"]}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QTextBrowser {{
            background: {color["bg.input"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["control"]}px; color: {color["text.primary"]}; padding: {SPACING["sm"]}px;
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QPushButton:focus {{ border: 2px solid {color["border.focus"]}; }}
        QTreeWidget, QListWidget, QTableWidget {{ background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]}; alternate-background-color: {color["bg.input"]}; }}
        QTreeWidget::item, QListWidget::item, QTableWidget::item {{ padding: {SPACING["sm"]}px; }}
        QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; }}
        QHeaderView::section {{ background: {color["bg.topbar"]}; color: {color["text.secondary"]}; border: 0; border-bottom: 1px solid {color["border.subtle"]}; padding: {SPACING["sm"]}px; }}
        QTabBar::tab {{ background: transparent; color: {color["text.secondary"]}; padding: {SPACING["sm"]}px {SPACING["lg"]}px; }}
        QTabBar::tab:selected {{ color: {color["accent.cyan"]}; border-bottom: 2px solid {color["accent.cyan"]}; }}
        QWidget#statusBadge {{ border-radius: {RADIUS["control"]}px; padding: 2px {SPACING["sm"]}px; font-weight: 600; }}
        QWidget#statusBadge[state="running"] {{ color: {color["state.info"]}; border: 1px solid {color["state.info"]}; }}
        QWidget#statusBadge[state="queued"], QWidget#statusBadge[state="partial"], QWidget#statusBadge[state="needs_approval"] {{ color: {color["state.warning"]}; border: 1px solid {color["state.warning"]}; }}
        QWidget#statusBadge[state="completed"] {{ color: {color["state.success"]}; border: 1px solid {color["state.success"]}; }}
        QWidget#statusBadge[state="failed"] {{ color: {color["state.danger"]}; border: 1px solid {color["state.danger"]}; }}
        QWidget#statusBadge[state="cancelled"], QWidget#statusBadge[state="unavailable"] {{ color: {color["text.muted"]}; border: 1px solid {color["border.subtle"]}; }}
        QStatusBar {{ background: {color["bg.topbar"]}; color: {color["text.secondary"]}; border-top: 1px solid {color["border.subtle"]}; }}
    """
