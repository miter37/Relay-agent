"""Application-wide palette and QSS generated from semantic design tokens."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from .design_tokens import COLORS, RADIUS, SPACING


def application_palette() -> QPalette:
    """Return an explicit palette so native Qt widgets never fall back to black text."""
    palette = QPalette()
    roles = {
        QPalette.Window: "bg.canvas",
        QPalette.Base: "bg.input",
        QPalette.AlternateBase: "bg.surface",
        QPalette.ToolTipBase: "bg.surfaceRaised",
        QPalette.ToolTipText: "text.primary",
        QPalette.Text: "text.primary",
        QPalette.WindowText: "text.primary",
        QPalette.Button: "bg.surfaceRaised",
        QPalette.ButtonText: "text.primary",
        QPalette.BrightText: "text.primary",
        QPalette.Highlight: "accent.primary",
        QPalette.HighlightedText: "text.primary",
        QPalette.Link: "accent.cyan",
    }
    for role, token in roles.items():
        palette.setColor(role, QColor(COLORS[token]))
    palette.setColor(QPalette.PlaceholderText, QColor(COLORS["text.muted"]))
    disabled = QPalette(palette)
    disabled.setColor(QPalette.Text, QColor(COLORS["text.muted"]))
    disabled.setColor(QPalette.WindowText, QColor(COLORS["text.muted"]))
    disabled.setColor(QPalette.ButtonText, QColor(COLORS["text.muted"]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, disabled.color(role))
    return palette


def application_stylesheet() -> str:
    """Return the single restrained dark-theme stylesheet installed by :mod:`gui.app`."""
    color = COLORS
    return f"""
        QApplication {{ color: {color["text.primary"]}; font-size: 13px; }}
        QWidget, QMainWindow {{ background: transparent; color: {color["text.primary"]}; font-size: 13px; }}
        QMainWindow, QDialog, QMessageBox {{ background: {color["bg.canvas"]}; }}
        QLabel, QCheckBox, QRadioButton, QGroupBox, QAbstractButton {{ color: {color["text.primary"]}; }}
        QLabel {{ background: transparent; }}
        QWidget#topBar {{ background: {color["bg.topbar"]}; border-bottom: 1px solid {color["border.subtle"]}; }}
        QWidget#sidebarNav {{ background: {color["bg.sidebar"]}; border-right: 1px solid {color["border.subtle"]}; }}
        QFrame#surface, QFrame#metricCard, QWidget#emptyState {{
            background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["panel"]}px;
        }}
        QLabel#emptyState {{ color: {color["text.secondary"]}; padding: {SPACING["xl"]}px; }}
        QLabel#pageTitle {{ color: {color["text.primary"]}; font-size: 22px; font-weight: 600; }}
        QLabel#detailTitle {{ color: {color["text.primary"]}; font-size: 18px; font-weight: 600; }}
        QLabel#sectionTitle {{ color: {color["text.primary"]}; font-size: 15px; font-weight: 600; }}
        QLabel#mutedText {{ color: {color["text.muted"]}; font-size: 12px; }}
        QLabel#emptyHint {{ color: {color["text.secondary"]}; background: {color["bg.surface"]}; border: 1px dashed {color["border.subtle"]}; border-radius: {RADIUS["panel"]}px; padding: {SPACING["xl"]}px; }}
        QLabel#doctorStatus {{ color: {color["text.muted"]}; }}
        QLabel#doctorStatus[tone="healthy"] {{ color: {color["state.success"]}; }}
        QLabel#doctorStatus[tone="failed"] {{ color: {color["state.danger"]}; }}
        QLabel#doctorStatus[tone="running"] {{ color: {color["state.info"]}; }}
        QLabel#errorText {{ color: {color["state.danger"]}; }}
        QLabel#healthBadge {{ border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; padding: 4px 10px; font-weight: 600; }}
        QLabel#healthBadge[tone="healthy"] {{ color: {color["state.success"]}; border-color: {color["state.success"]}; background: {color["bg.surface"]}; }}
        QLabel#healthBadge[tone="attention"], QLabel#healthBadge[tone="checking"] {{ color: {color["state.warning"]}; border-color: {color["state.warning"]}; background: {color["bg.surface"]}; }}
        QLabel#healthBadge[tone="unhealthy"], QLabel#healthBadge[tone="disconnected"] {{ color: {color["state.danger"]}; border-color: {color["state.danger"]}; background: {color["bg.surface"]}; }}
        QPushButton {{
            background: {color["bg.surfaceRaised"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["control"]}px; color: {color["text.primary"]}; padding: {SPACING["sm"]}px {SPACING["md"]}px;
        }}
        QPushButton:hover {{ border-color: {color["accent.cyan"]}; background: {color["bg.surface"]}; }}
        QPushButton:disabled {{ color: {color["text.muted"]}; background: {color["bg.input"]}; }}
        QPushButton#primaryAction {{ background: {color["accent.primary"]}; border-color: {color["accent.primary"]}; color: {color["text.primary"]}; font-weight: 700; }}
        QPushButton#primaryAction:hover {{ background: {color["accent.primary"]}; border-color: {color["border.focus"]}; }}
        QPushButton:pressed {{ background: {color["bg.input"]}; }}
        QPushButton#dangerAction {{ color: {color["state.danger"]}; }}
        QPushButton#sidebarButton {{ text-align: left; background: transparent; border: 0; border-radius: {RADIUS["control"]}px; }}
        QPushButton#sidebarButton:hover, QPushButton#sidebarButton:checked {{ background: {color["bg.surfaceRaised"]}; color: {color["accent.cyan"]}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QTextBrowser, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
            background: {color["bg.input"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["control"]}px; color: {color["text.primary"]}; padding: {SPACING["sm"]}px;
        }}
        QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only {{ background: {color["bg.surface"]}; }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus, QPushButton:focus, QCheckBox:focus, QRadioButton:focus {{ border: 2px solid {color["border.focus"]}; }}
        QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QPushButton:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {color["text.muted"]}; background: {color["bg.surface"]}; border-color: {color["border.subtle"]}; }}
        QComboBox::drop-down {{ width: 24px; border: 0; border-left: 1px solid {color["border.subtle"]}; }}
        QComboBox QAbstractItemView {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; border: 1px solid {color["border.subtle"]}; selection-background-color: {color["accent.primary"]}; selection-color: {color["text.primary"]}; }}
        QTreeWidget, QListWidget, QTableWidget {{ background: {color["bg.surface"]}; color: {color["text.primary"]}; border: 1px solid {color["border.subtle"]}; alternate-background-color: {color["bg.input"]}; gridline-color: {color["border.subtle"]}; }}
        QTreeWidget::item, QListWidget::item, QTableWidget::item {{ padding: {SPACING["sm"]}px; }}
        QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; }}
        QHeaderView::section {{ background: {color["bg.topbar"]}; color: {color["text.secondary"]}; border: 0; border-bottom: 1px solid {color["border.subtle"]}; padding: {SPACING["sm"]}px; }}
        QTabBar::tab {{ background: transparent; color: {color["text.secondary"]}; padding: {SPACING["sm"]}px {SPACING["lg"]}px; }}
        QTabBar::tab:selected {{ color: {color["accent.cyan"]}; border-bottom: 2px solid {color["accent.cyan"]}; }}
        QTabWidget::pane {{ background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["panel"]}px; }}
        QTextBrowser#evidencePane {{ background: {color["bg.surface"]}; border: 0; padding: {SPACING["md"]}px; }}
        QLabel#statusBadge {{ border-radius: {RADIUS["control"]}px; padding: 2px {SPACING["sm"]}px; font-weight: 600; background: {color["bg.surface"]}; }}
        QLabel#statusBadge[state="running"] {{ color: {color["state.info"]}; border: 1px solid {color["state.info"]}; }}
        QLabel#statusBadge[state="queued"], QLabel#statusBadge[state="partial"], QLabel#statusBadge[state="needs_approval"] {{ color: {color["state.warning"]}; border: 1px solid {color["state.warning"]}; }}
        QLabel#statusBadge[state="completed"] {{ color: {color["state.success"]}; border: 1px solid {color["state.success"]}; }}
        QLabel#statusBadge[state="failed"] {{ color: {color["state.danger"]}; border: 1px solid {color["state.danger"]}; }}
        QLabel#statusBadge[state="cancelled"], QLabel#statusBadge[state="unavailable"] {{ color: {color["text.muted"]}; border: 1px solid {color["border.subtle"]}; }}
        QFrame#inlineNotice {{ background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; }}
        QFrame#inlineNotice[tone="running"], QFrame#inlineNotice[tone="info"] {{ border-color: {color["state.info"]}; }}
        QFrame#inlineNotice[tone="queued"], QFrame#inlineNotice[tone="partial"], QFrame#inlineNotice[tone="needs_approval"] {{ border-color: {color["state.warning"]}; }}
        QFrame#inlineNotice[tone="failed"] {{ border-color: {color["state.danger"]}; }}
        QGroupBox {{ border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; margin-top: 12px; padding: 12px 8px 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {color["text.secondary"]}; }}
        QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; border: 1px solid {color["border.subtle"]}; background: {color["bg.input"]}; }}
        QCheckBox::indicator {{ border-radius: 3px; }}
        QRadioButton::indicator {{ border-radius: 8px; }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {color["accent.primary"]}; border-color: {color["border.focus"]}; }}
        QMenu {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; border: 1px solid {color["border.subtle"]}; padding: 4px; }}
        QMenu::item {{ padding: 7px 24px 7px 10px; }}
        QMenu::item:selected {{ background: {color["accent.primary"]}; color: {color["text.primary"]}; }}
        QToolTip {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; border: 1px solid {color["border.focus"]}; padding: 5px; }}
        QProgressBar {{ background: {color["bg.input"]}; border: 1px solid {color["border.subtle"]}; color: {color["text.primary"]}; text-align: center; border-radius: {RADIUS["control"]}px; }}
        QProgressBar::chunk {{ background: {color["accent.primary"]}; border-radius: {RADIUS["control"]}px; }}
        QScrollBar:vertical, QScrollBar:horizontal {{ background: {color["bg.input"]}; border: 0; margin: 0; }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {color["border.subtle"]}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
        QScrollBar::handle:hover {{ background: {color["text.muted"]}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ background: transparent; border: 0; }}
        QSplitter::handle {{ background: {color["border.subtle"]}; }}
        QTextBrowser {{ selection-background-color: {color["accent.primary"]}; selection-color: {color["text.primary"]}; }}
        QTextBrowser a {{ color: {color["accent.cyan"]}; }}
        QStatusBar {{ background: {color["bg.topbar"]}; color: {color["text.secondary"]}; border-top: 1px solid {color["border.subtle"]}; }}
    """
