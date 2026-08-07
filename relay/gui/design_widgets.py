"""Small reusable widgets that enforce Relay's shared visual grammar."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .design_icons import icon
from .design_tokens import METRICS, SPACING, status_presentation
from .design_typography import apply_type


class StatusBadge(QLabel):
    """A labelled semantic state badge; state is never color-only."""

    def __init__(self, status: object = "unavailable", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignCenter)
        apply_type(self, "overline")
        self.set_status(status)

    def set_status(self, status: object) -> None:
        presentation = status_presentation(status)
        self.setProperty("state", presentation.state)
        self.setText(presentation.label)
        self.setToolTip(presentation.label)
        self.style().unpolish(self)
        self.style().polish(self)


class IconButton(QPushButton):
    """A square icon-only action button with a mandatory tooltip and accessible name.

    ``tone`` selects the icon tint and the QSS hover accent: ``default``,
    ``accent``, or ``danger``. The tooltip text doubles as the accessible
    name so screen readers announce the same thing a sighted user hovers.
    """

    def __init__(
        self, icon_name: str, tooltip: str, *, tone: str = "default", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("iconAction")
        self.setProperty("tone", tone)
        self._icon_name = icon_name
        self._tone = tone
        self.setIcon(icon(icon_name, tone))
        self.setIconSize(QSize(METRICS["iconSize"], METRICS["iconSize"]))
        self.setFixedSize(METRICS["iconButton"], METRICS["iconButton"])
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)

    def set_tooltip(self, tooltip: str) -> None:
        """Update both the tooltip and the accessible name together."""
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)


class NavButton(QPushButton):
    """A checkable primary-navigation item: icon + label, left-edge active indicator."""

    def __init__(self, icon_name: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setObjectName("sidebarButton")
        self.setCheckable(True)
        self.setIcon(icon(icon_name, "default"))
        self.setIconSize(QSize(METRICS["navIconSize"], METRICS["navIconSize"]))
        self.setCursor(Qt.PointingHandCursor)
        apply_type(self, "body")


class LabeledButton(QPushButton):
    """A button combining a leading icon with a text label.

    ``tone`` of ``primary`` sets ``objectName("primaryAction")``; anything
    else leaves the button as a neutral secondary action.
    """

    def __init__(
        self, icon_name: str, text: str, *, tone: str = "secondary", parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent)
        if tone == "primary":
            self.setObjectName("primaryAction")
            apply_type(self, "body.strong")
        else:
            apply_type(self, "body")
        self.setIcon(icon(icon_name, "onPrimary" if tone == "primary" else "default"))
        self.setIconSize(QSize(METRICS["iconSize"], METRICS["iconSize"]))
        self.setCursor(Qt.PointingHandCursor)


class SectionHeader(QWidget):
    """Compact section title with an optional right-aligned action widget."""

    def __init__(
        self, title: str, subtitle: str = "", action: QWidget | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        labels = QVBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        apply_type(title_label, "title.section")
        labels.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("mutedText")
            apply_type(subtitle_label, "caption")
            labels.addWidget(subtitle_label)
        layout.addLayout(labels, 1)
        if action is not None:
            layout.addWidget(action)


class MetricCard(QFrame):
    """One operational value, label, and short qualifier."""

    def __init__(self, label: str, value: str, qualifier: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["lg"], SPACING["md"], SPACING["lg"], SPACING["md"])
        label_widget = QLabel(label)
        label_widget.setObjectName("mutedText")
        apply_type(label_widget, "caption")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        apply_type(self.value_label, "title.page")
        self.qualifier_label = QLabel(qualifier)
        self.qualifier_label.setObjectName("mutedText")
        apply_type(self.qualifier_label, "caption")
        layout.addWidget(label_widget)
        layout.addWidget(self.value_label)
        layout.addWidget(self.qualifier_label)


class EmptyState(QWidget):
    """Explain an empty operational area and provide one safe next action."""

    action_requested = Signal()

    def __init__(self, title: str, description: str, action_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["xl"], SPACING["xl"], SPACING["xl"], SPACING["xl"])
        layout.setAlignment(Qt.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setAlignment(Qt.AlignCenter)
        apply_type(title_label, "title.section")
        self.description_label = QLabel(description)
        self.description_label.setObjectName("mutedText")
        self.description_label.setWordWrap(True)
        self.description_label.setAlignment(Qt.AlignCenter)
        apply_type(self.description_label, "caption")
        self.action_button = QPushButton(action_text)
        self.action_button.setObjectName("primaryAction")
        apply_type(self.action_button, "body.strong")
        self.action_button.setVisible(bool(action_text))
        self.action_button.clicked.connect(self.action_requested.emit)
        layout.addWidget(title_label)
        layout.addWidget(self.description_label)
        layout.addWidget(self.action_button, alignment=Qt.AlignCenter)


class InlineNotice(QFrame):
    """A short textual notice for success, caution, or daemon errors."""

    def __init__(self, message: str = "", tone: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inlineNotice")
        self.label = QLabel(message)
        self.label.setWordWrap(True)
        apply_type(self.label, "body")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"])
        layout.addWidget(self.label)
        self.set_notice(message, tone)

    def set_notice(self, message: str, tone: str = "info") -> None:
        presentation = status_presentation(tone)
        self.setProperty("tone", presentation.state)
        self.label.setText(message)
        self.style().unpolish(self)
        self.style().polish(self)
