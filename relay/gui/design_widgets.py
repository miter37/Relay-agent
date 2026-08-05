"""Small reusable widgets that enforce Relay's shared visual grammar."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .design_tokens import SPACING, status_presentation


class StatusBadge(QLabel):
    """A labelled semantic state badge; state is never color-only."""

    def __init__(self, status: object = "unavailable", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignCenter)
        self.set_status(status)

    def set_status(self, status: object) -> None:
        presentation = status_presentation(status)
        self.setProperty("state", presentation.state)
        self.setText(presentation.label)
        self.setToolTip(presentation.label)
        self.style().unpolish(self)
        self.style().polish(self)


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
        labels.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("mutedText")
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
        self.value_label = QLabel(value)
        self.value_label.setObjectName("pageTitle")
        self.qualifier_label = QLabel(qualifier)
        self.qualifier_label.setObjectName("mutedText")
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
        self.description_label = QLabel(description)
        self.description_label.setObjectName("mutedText")
        self.description_label.setWordWrap(True)
        self.description_label.setAlignment(Qt.AlignCenter)
        self.action_button = QPushButton(action_text)
        self.action_button.setObjectName("primaryAction")
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
