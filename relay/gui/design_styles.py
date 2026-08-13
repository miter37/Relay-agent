"""Application-wide palette and QSS generated from semantic design tokens."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

from .design_tokens import COLORS, METRICS, RADIUS, SPACING


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
        QPalette.Link: "accent.primary",
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
    """Return the single restrained dark-theme stylesheet installed by :mod:`gui.app`.

    Typography (font-family/size/weight/letter-spacing) is deliberately absent
    here; QSS font-size overrides a widget's QFont and QSS does not support
    letter-spacing at all. Every widget's font is set once via
    :func:`relay.gui.design_typography.apply_type`. The only exceptions are
    sub-control selectors that cannot receive a QFont directly (marked below).
    """
    color = COLORS
    return f"""
        QApplication {{ color: {color["text.primary"]}; }}
        QWidget, QMainWindow {{ background: transparent; color: {color["text.primary"]}; }}
        QMainWindow, QDialog, QMessageBox {{ background: {color["bg.canvas"]}; }}
        QLabel, QCheckBox, QRadioButton, QGroupBox, QAbstractButton {{ color: {color["text.primary"]}; }}
        QLabel {{ background: transparent; }}
        QWidget#topBar {{ background: {color["bg.topbar"]}; border-bottom: 1px solid {color["border.subtle"]}; min-height: {METRICS["topBarHeight"]}px; }}
        QWidget#sidebarNav {{ background: {color["bg.sidebar"]}; border-right: 1px solid {color["border.subtle"]}; }}
        QFrame#surface, QFrame#metricCard, QWidget#emptyState {{
            background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["panel"]}px;
        }}
        QLabel#emptyState {{ color: {color["text.secondary"]}; padding: {SPACING["xl"]}px; }}
        QLabel#brandMark {{ color: {color["accent.primary"]}; }}
        QLabel#pageTitle {{ color: {color["text.secondary"]}; }}
        QLabel#detailTitle {{ color: {color["text.primary"]}; }}
        QLabel#sectionTitle {{ color: {color["text.primary"]}; }}
        QLabel#mutedText {{ color: {color["text.muted"]}; }}
        QLabel#dataText {{ color: {color["text.secondary"]}; }}
        QLabel#emptyHint {{ color: {color["text.secondary"]}; background: {color["bg.surface"]}; border: 1px dashed {color["border.subtle"]}; border-radius: {RADIUS["panel"]}px; padding: {SPACING["xl"]}px; }}
        QLabel#doctorStatus {{ color: {color["text.muted"]}; }}
        QLabel#doctorStatus[tone="healthy"] {{ color: {color["state.success"]}; }}
        QLabel#doctorStatus[tone="failed"] {{ color: {color["state.danger"]}; }}
        QLabel#doctorStatus[tone="running"] {{ color: {color["state.info"]}; }}
        QLabel#errorText {{ color: {color["state.danger"]}; }}
        /* Status text, not a control: no border or fill, so it never reads as a button. */
        QLabel#healthBadge {{ border: 0; background: transparent; padding: 0; color: {color["text.secondary"]}; }}
        QLabel#healthBadge[tone="healthy"] {{ color: {color["text.secondary"]}; }}
        QLabel#healthBadge[tone="attention"], QLabel#healthBadge[tone="checking"] {{ color: {color["state.warning"]}; }}
        QLabel#healthBadge[tone="unhealthy"], QLabel#healthBadge[tone="disconnected"] {{ color: {color["state.danger"]}; }}
        QLabel#healthDot {{ border: 0; background: transparent; padding: 0; color: {color["text.muted"]}; }}
        QLabel#healthDot[tone="healthy"] {{ color: {color["state.success"]}; }}
        QLabel#healthDot[tone="attention"], QLabel#healthDot[tone="checking"] {{ color: {color["state.warning"]}; }}
        QLabel#healthDot[tone="unhealthy"], QLabel#healthDot[tone="disconnected"] {{ color: {color["state.danger"]}; }}
        QPushButton {{
            background: {color["bg.surfaceRaised"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["control"]}px; color: {color["text.primary"]}; padding: {SPACING["sm"]}px {SPACING["md"]}px;
            min-height: {METRICS["controlHeight"]}px;
        }}
        QPushButton:hover {{ border-color: {color["border.strong"]}; background: {color["bg.hover"]}; }}
        QPushButton:pressed {{ background: {color["bg.pressed"]}; }}
        QPushButton#jsonModeToggle:checked {{ background: {color["bg.selected"]}; border-color: {color["accent.primary"]}; color: {color["text.primary"]}; }}
        QFrame#segmentedControl {{ background: {color["bg.input"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; }}
        QPushButton#segmentedButton {{ background: transparent; border: 0; border-radius: {RADIUS["control"] - 2}px; color: {color["text.secondary"]}; padding: 4px 12px; min-height: 22px; }}
        QPushButton#segmentedButton:hover {{ background: {color["bg.hover"]}; color: {color["text.primary"]}; }}
        QPushButton#segmentedButton:checked {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; border: 1px solid {color["border.strong"]}; }}
        QPushButton:disabled {{ color: {color["text.muted"]}; background: {color["bg.input"]}; }}
        QPushButton#primaryAction {{ background: {color["action.primaryBg"]}; border-color: {color["action.primaryBg"]}; color: {color["action.primaryFg"]}; }}
        QPushButton#primaryAction:hover {{ background: {color["action.primaryBg"]}; border-color: {color["border.focus"]}; }}
        QPushButton#primaryAction:pressed {{ background: {color["text.secondary"]}; }}
        QPushButton#primaryAction:disabled {{ background: {color["bg.surfaceRaised"]}; color: {color["text.muted"]}; border-color: {color["border.subtle"]}; }}
        QPushButton#dangerAction {{ color: {color["state.danger"]}; }}
        QPushButton#dangerAction:hover {{ border-color: {color["state.danger"]}; background: {color["bg.hover"]}; }}
        QPushButton#sidebarButton {{ text-align: left; background: transparent; border: 0; border-left: 2px solid transparent; border-radius: 0; padding: {SPACING["sm"]}px {SPACING["md"]}px; }}
        QPushButton#sidebarButton:hover {{ background: {color["bg.hover"]}; color: {color["text.primary"]}; }}
        QPushButton#sidebarButton:checked {{ background: {color["bg.hover"]}; color: {color["text.primary"]}; border-left: 2px solid {color["accent.primary"]}; }}
        QPushButton#iconAction {{
            background: transparent; border: 1px solid transparent; border-radius: {RADIUS["control"]}px;
            padding: 0; min-width: {METRICS["iconButton"]}px; max-width: {METRICS["iconButton"]}px;
            min-height: {METRICS["iconButton"]}px; max-height: {METRICS["iconButton"]}px;
        }}
        QPushButton#iconAction:hover {{ background: {color["bg.hover"]}; }}
        QPushButton#iconAction:pressed {{ background: {color["bg.pressed"]}; }}
        QPushButton#iconAction:disabled {{ background: transparent; }}
        QPushButton#iconAction:focus {{ border: 1px solid {color["border.focus"]}; }}
        QPushButton#iconAction[tone="danger"]:hover {{ background: {color["bg.hover"]}; border-color: {color["state.danger"]}; }}
        QPushButton#iconAction[tone="accent"]:hover {{ background: {color["bg.hover"]}; border-color: {color["accent.primary"]}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QTextBrowser, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
            background: {color["bg.input"]}; border: 1px solid {color["border.subtle"]};
            border-radius: {RADIUS["control"]}px; color: {color["text.primary"]};
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{ padding: {SPACING["sm"]}px; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{ min-height: {METRICS["controlHeight"]}px; }}
        /* QComboBox gets no vertical padding, unlike the other controls above,
           and `max-height` is deliberately absent here: Qt's QStyleSheetStyle
           does not actually clamp QComboBox to a stylesheet max-height (verified
           empirically - it was silently ignored), and its rendered height is
           `font_content_height + 2*vertical_padding` regardless. Any vertical
           padding compounds with the font's own content height, and a CJK-script
           item (e.g. a Korean Task name) needs a taller fallback-font content
           height than Latin text - stacking padding on top of that pushed the
           combo well past the picker row's fixed height and into the row below.
           Horizontal padding is kept (it doesn't affect height); the combo's
           built-in frame/arrow already reserve enough vertical room on their own. */
        QComboBox {{ padding: 0 {SPACING["sm"]}px; }}
        QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only {{ background: {color["bg.surface"]}; }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{ border: 1px solid {color["border.focus"]}; }}
        QPushButton:focus, QCheckBox:focus, QRadioButton:focus {{ border-color: {color["border.focus"]}; }}
        QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QPushButton:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {color["text.muted"]}; background: {color["bg.surface"]}; border-color: {color["border.subtle"]}; }}
        /* No ::drop-down override: overriding it suppresses the style's own arrow,
           and QSS can only supply a replacement through an on-disk image URL. */
        QComboBox QAbstractItemView {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; border: 1px solid {color["border.subtle"]}; selection-background-color: {color["bg.selected"]}; selection-color: {color["text.primary"]}; }}
        QTreeWidget, QListWidget, QTableWidget {{ background: {color["bg.surface"]}; color: {color["text.primary"]}; border: 1px solid {color["border.subtle"]}; alternate-background-color: {color["bg.input"]}; gridline-color: {color["border.subtle"]}; }}
        QTreeWidget::item, QListWidget::item, QTableWidget::item {{ padding: {METRICS["rowPadding"]}px {SPACING["sm"]}px; min-height: {METRICS["rowHeight"]}px; }}
        QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {{ background: {color["bg.selected"]}; color: {color["text.primary"]}; }}
        QTreeWidget::item:hover, QListWidget::item:hover, QTableWidget::item:hover {{ background: {color["bg.hover"]}; }}
        QHeaderView::section {{ background: {color["bg.topbar"]}; color: {color["text.secondary"]}; border: 0; border-bottom: 1px solid {color["border.subtle"]}; padding: {SPACING["sm"]}px; font-size: 11px; }} /* type-scale exception: header sub-control cannot receive QFont */
        QTabBar::tab {{ background: transparent; color: {color["text.secondary"]}; padding: {SPACING["sm"]}px {SPACING["lg"]}px; font-size: 13px; }} /* type-scale exception: tab sub-control cannot receive QFont */
        QTabBar::tab:selected {{ color: {color["text.primary"]}; border-bottom: 2px solid {color["accent.primary"]}; }}
        QTabWidget::pane {{ background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["panel"]}px; }}
        QTextBrowser#evidencePane {{ background: {color["bg.surface"]}; border: 0; padding: {SPACING["md"]}px; }}
        QTextBrowser#overviewEvidencePane {{ background: {color["bg.surface"]}; border: 0; padding: {SPACING["xs"]}px {SPACING["md"]}px {SPACING["md"]}px; }}
        QWidget#artifactListPanel {{ background: {color["bg.surface"]}; border-right: 1px solid {color["border.subtle"]}; padding-right: {SPACING["md"]}px; }}
        QFrame#artifactMetadata {{ background: {color["bg.input"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; }}
        QLabel#artifactPath {{ color: {color["text.secondary"]}; }}
        QLabel#artifactFormat {{ color: {color["text.muted"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["badge"]}px; padding: 2px 6px; }}
        QLabel#artifactStatus {{ border-radius: {RADIUS["badge"]}px; padding: 2px 7px; border: 1px solid {color["border.subtle"]}; }}
        QLabel#artifactStatus[state="candidate"] {{ color: {color["state.warning"]}; border-color: {color["state.warning"]}; }}
        QLabel#artifactStatus[state="completed"] {{ color: {color["state.success"]}; border-color: {color["state.success"]}; }}
        QLabel#artifactStatus[state="failed"] {{ color: {color["state.danger"]}; border-color: {color["state.danger"]}; }}
        QLabel#artifactStatus[state="muted"] {{ color: {color["text.muted"]}; border-color: {color["border.subtle"]}; }}
        QStackedWidget#artifactPreviewSurface {{ background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["panel"]}px; }}
        QFrame#pipelineNodeCard {{ background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; }}
        QFrame#pipelineNodeCard[pipelineState="completed"] {{ border-color: {color["state.success"]}; }}
        QFrame#pipelineNodeCard[pipelineState="running"] {{ border-color: {color["accent.relay"]}; }}
        QFrame#pipelineNodeCard[pipelineState="queued"], QFrame#pipelineNodeCard[pipelineState="accepted"], QFrame#pipelineNodeCard[pipelineState="awaiting_approval"], QFrame#pipelineNodeCard[pipelineState="awaiting_review"] {{ border-color: {color["state.warning"]}; }}
        QFrame#pipelineNodeCard[pipelineState="failed"] {{ border-color: {color["state.danger"]}; }}
        QFrame#pipelineNodeCard[pipelineState="blocked"] {{ border: 1px dashed {color["text.muted"]}; }}
        QFrame#pipelineNodeCard[pipelineState="cancelled"] {{ border-color: {color["text.muted"]}; }}
        QFrame#pipelineNodeCard[pipelineSelected="true"] {{ border-width: 2px; border-color: {color["accent.primary"]}; }}
        QLabel#statusBadge {{
            border-radius: {RADIUS["badge"]}px; padding: 2px {SPACING["sm"]}px 2px {SPACING["md"]}px;
            background: {color["bg.surface"]}; border: 0; border-left: 3px solid {color["border.subtle"]};
        }}
        QLabel#statusBadge[state="running"] {{ color: {color["state.info"]}; border-left-color: {color["state.info"]}; }}
        QLabel#statusBadge[state="queued"], QLabel#statusBadge[state="partial"], QLabel#statusBadge[state="needs_approval"], QLabel#statusBadge[state="needs_review"] {{ color: {color["state.warning"]}; border-left-color: {color["state.warning"]}; }}
        QLabel#statusBadge[state="completed"] {{ color: {color["state.success"]}; border-left-color: {color["state.success"]}; }}
        QLabel#statusBadge[state="failed"] {{ color: {color["state.danger"]}; border-left-color: {color["state.danger"]}; }}
        QLabel#statusBadge[state="cancelled"], QLabel#statusBadge[state="unavailable"] {{ color: {color["text.muted"]}; border-left-color: {color["border.subtle"]}; }}
        QFrame#inlineNotice {{ background: {color["bg.surface"]}; border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; }}
        QFrame#inlineNotice[tone="running"], QFrame#inlineNotice[tone="info"] {{ border-color: {color["state.info"]}; }}
        QFrame#inlineNotice[tone="queued"], QFrame#inlineNotice[tone="partial"], QFrame#inlineNotice[tone="needs_approval"] {{ border-color: {color["state.warning"]}; }}
        QFrame#inlineNotice[tone="failed"] {{ border-color: {color["state.danger"]}; }}
        QGroupBox {{ border: 1px solid {color["border.subtle"]}; border-radius: {RADIUS["control"]}px; margin-top: 12px; padding: 12px 8px 8px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {color["text.secondary"]}; }}
        /* No ::indicator override: styling it suppresses the style's own check/dot
           glyph, which QSS can only replace through an on-disk image URL, leaving
           a checked box indistinguishable from an unchecked one. */
        QMenu {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; border: 1px solid {color["border.subtle"]}; padding: 4px; }}
        QMenu::item {{ padding: 7px 24px 7px 10px; font-size: 13px; }} /* type-scale exception: menu sub-control cannot receive QFont */
        QMenu::item:selected {{ background: {color["bg.selected"]}; color: {color["text.primary"]}; }}
        QToolTip {{ background: {color["bg.surfaceRaised"]}; color: {color["text.primary"]}; border: 1px solid {color["border.strong"]}; padding: 5px; }}
        QProgressBar {{ background: {color["bg.input"]}; border: 1px solid {color["border.subtle"]}; color: {color["text.primary"]}; text-align: center; border-radius: {RADIUS["control"]}px; }}
        QProgressBar::chunk {{ background: {color["accent.primary"]}; border-radius: {RADIUS["control"]}px; }}
        QScrollBar:vertical, QScrollBar:horizontal {{ background: {color["bg.input"]}; border: 0; margin: 0; }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {color["border.subtle"]}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
        QScrollBar::handle:hover {{ background: {color["text.muted"]}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ background: transparent; border: 0; }}
        QSplitter::handle {{ background: {color["border.subtle"]}; }}
        QTextBrowser {{ selection-background-color: {color["bg.selected"]}; selection-color: {color["text.primary"]}; }}
        QTextBrowser a {{ color: {color["accent.primary"]}; }}
        QStatusBar {{ background: {color["bg.topbar"]}; color: {color["text.secondary"]}; border-top: 1px solid {color["border.subtle"]}; }}
    """
