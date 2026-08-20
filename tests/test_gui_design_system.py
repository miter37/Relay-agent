"""Offscreen regression tests for Relay's shared GUI design grammar."""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QLabel, QTableWidgetItem
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.design_icon_app import app_icon
from relay.gui.design_styles import application_palette, application_stylesheet
from relay.gui.design_tokens import COLORS, contrast_ratio, status_presentation
from relay.gui.design_typography import TYPE_SCALE, application_font, font_for
from relay.gui.design_widgets import EmptyState, MetricCard, StatusBadge, apply_data_style, style_data_table_item


class DesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setFont(application_font())
        cls.app.setPalette(application_palette())
        cls.app.setStyleSheet(application_stylesheet())

    def test_status_badge_uses_shared_semantic_state_and_text(self):
        badge = StatusBadge("RUNNING")

        self.assertEqual(badge.text(), "Running")
        self.assertEqual(badge.property("state"), "running")
        self.assertEqual(badge.styleSheet(), "")
        self.assertIn('QLabel#statusBadge[state="running"]', application_stylesheet())
        self.assertEqual(status_presentation("awaiting_review").state, "needs_review")
        self.assertEqual(status_presentation("awaiting_review").label, "Needs review")
        self.assertIn('QFrame#pipelineNodeCard[pipelineState="completed"]', application_stylesheet())
        self.assertEqual(status_presentation("unknown").label, "Unavailable")

    def test_accent_relay_is_reserved_and_distinct_from_the_interactive_accent(self):
        self.assertIn("accent.relay", COLORS)
        self.assertIn("accent.brand", COLORS)
        self.assertNotEqual(COLORS["accent.relay"], COLORS["accent.primary"])
        self.assertNotEqual(COLORS["accent.brand"], COLORS["accent.primary"])
        self.assertNotEqual(COLORS["accent.brand"], COLORS["accent.relay"])

    def test_control_and_row_heights_have_room_for_body_text(self):
        from relay.gui.design_tokens import METRICS, SPACING

        body_size = TYPE_SCALE["body"].size
        self.assertGreaterEqual(METRICS["controlHeight"], body_size + 2 * SPACING["xs"])
        self.assertGreaterEqual(METRICS["rowHeight"], body_size + 2 * SPACING["xs"])

    def test_control_and_row_heights_are_derived_from_the_type_scale_not_hand_picked(self):
        # Recomputes the derivation formula independently; a future hand-edit
        # back to an arbitrary magic number would fail this loudly.
        from relay.gui.design_tokens import METRICS, SPACING

        expected_line_height = round(TYPE_SCALE["body"].size * 1.4)
        expected_row_height = expected_line_height + 2 * SPACING["xs"]
        expected_control_height = expected_line_height + 2 * (SPACING["xs"] + 1)
        self.assertEqual(METRICS["rowHeight"], expected_row_height)
        self.assertEqual(METRICS["controlHeight"], expected_control_height)

    def test_combobox_has_no_vertical_padding_stylesheet_rule(self):
        # QComboBox.sizeHint() grows with whatever fallback font Qt selects for
        # the current item text - CJK text (e.g. a Korean Task name) reports a
        # taller content height than the Latin text this was first verified
        # against, and any vertical padding on top of that pushed a real
        # picker-table combo to 46px inside a 36px fixed row (reproduced against
        # a live registered Project). QSS `max-height` does not clamp QComboBox
        # at all (verified empirically), so the only actual fix is giving
        # QComboBox zero vertical padding so the font-driven content height is
        # all there is. This can't be verified by rendering pixel heights in
        # this offscreen-Qt test harness (no real fonts, no CJK metric
        # difference to reproduce - see memo.md); it instead pins the QSS rule
        # itself so this can't silently regress back to shared padding.
        css = application_stylesheet()
        match = re.search(r"QComboBox\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "no dedicated QComboBox rule found")
        combobox_rule = match.group(1)
        self.assertIn("padding: 0", combobox_rule)

    def test_data_style_applies_the_shared_id_hash_duration_convention(self):
        label = QLabel("01KZQA...")
        apply_data_style(label)
        self.assertEqual(label.objectName(), "dataText")
        self.assertIn("QLabel#dataText", application_stylesheet())

        item = QTableWidgetItem("01KZQA...")
        style_data_table_item(item)
        self.assertEqual(item.font().pixelSize(), TYPE_SCALE["data"].size)
        self.assertEqual(item.foreground().color().name().upper(), COLORS["text.secondary"])

    def test_metric_card_has_a_raised_elevation_effect(self):
        card = MetricCard("Label", "42", "qualifier")
        self.assertIsNotNone(card.graphicsEffect())

    def test_empty_state_explains_next_safe_action(self):
        state = EmptyState("No Task Runs yet", "Run a Task to create the first traceable execution.", "New Task")

        self.assertEqual(state.objectName(), "emptyState")
        self.assertEqual(state.action_button.text(), "New Task")
        self.assertIn("traceable execution", state.description_label.text())

    def test_application_stylesheet_covers_shell_focus_and_status_variants(self):
        stylesheet = application_stylesheet()

        self.assertIn("#sidebarNav", stylesheet)
        self.assertIn("#topBar", stylesheet)
        self.assertIn("QPushButton#primaryAction", stylesheet)
        self.assertIn('QLabel#statusBadge[state="running"]', stylesheet)
        self.assertIn(COLORS["border.focus"], stylesheet)
        surfaces = ("bg.canvas", "bg.surface", "bg.surfaceRaised", "bg.input", "bg.hover", "bg.pressed")
        for surface in surfaces:
            self.assertGreaterEqual(contrast_ratio(COLORS["text.primary"], COLORS[surface]), 4.5)
            self.assertGreaterEqual(contrast_ratio(COLORS["text.secondary"], COLORS[surface]), 4.5)
            self.assertGreaterEqual(contrast_ratio(COLORS["text.muted"], COLORS[surface]), 4.5)
        self.assertGreaterEqual(contrast_ratio(COLORS["accent.onPrimary"], COLORS["accent.primary"]), 4.5)
        self.assertGreaterEqual(contrast_ratio(COLORS["action.primaryFg"], COLORS["action.primaryBg"]), 4.5)

    def test_stylesheet_avoids_qt_unsupported_css_properties(self):
        stylesheet = application_stylesheet()

        for unsupported in ("letter-spacing", "line-height", "box-shadow", "transition", "text-transform"):
            self.assertNotIn(unsupported, stylesheet)

    def test_stylesheet_font_size_limited_to_documented_subcontrol_exceptions(self):
        stylesheet = application_stylesheet()

        occurrences = stylesheet.count("font-size")
        exceptions = stylesheet.count("type-scale exception")
        self.assertEqual(occurrences, exceptions)

    def test_type_scale_roles_produce_usable_fonts(self):
        for role in TYPE_SCALE:
            font = font_for(role)
            self.assertNotEqual(font.families(), [])
            self.assertNotEqual(font.families()[0], "")
            self.assertGreater(font.pixelSize(), 0)

    def test_application_palette_pins_default_text_and_input_roles(self):
        palette = application_palette()
        self.assertEqual(palette.color(QPalette.WindowText).name().upper(), COLORS["text.primary"])
        self.assertEqual(palette.color(QPalette.Base).name().upper(), COLORS["bg.input"])
        self.assertEqual(palette.color(QPalette.PlaceholderText).name().upper(), COLORS["text.muted"])

    def test_main_window_marks_the_shared_shell_and_primary_action(self):
        from relay.compatibility import relay_home_id
        from relay.config import Config
        from relay.gui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as temp:
            config = Config(Path(temp) / "home")
            config.init()
            window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
            try:
                self.assertEqual(window.top_bar.objectName(), "topBar")
                self.assertEqual(window.sidebar.objectName(), "sidebarNav")
                self.assertEqual(window.register_task_button.objectName(), "iconAction")
                self.assertEqual(window.register_task_button.accessibleName(), "Register a new Task")
                self.assertEqual(window.tasks_button.objectName(), "sidebarButton")
                window._show_tasks()
                self.assertTrue(window.tasks_button.isChecked())
                self.assertFalse(window.runs_button.isChecked())
                self.assertEqual(window.page_title_label.text(), "Tasks")
                window._show_runs()
                self.assertTrue(window.runs_button.isChecked())
                self.assertEqual(window.page_title_label.text(), "Runs")
            finally:
                window.close()

    def test_main_window_shows_a_permanent_relay_wordmark(self):
        from relay.compatibility import relay_home_id
        from relay.config import Config
        from relay.gui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as temp:
            config = Config(Path(temp) / "home")
            config.init()
            window = MainWindow(config, gui_version="1.1.0", expected_home_id=relay_home_id(config.home))
            try:
                self.assertEqual(window.brand_label.objectName(), "brandMark")
                self.assertEqual(window.brand_label.text(), "Relay")
                self.assertEqual(window.brand_icon.objectName(), "brandIcon")
                self.assertFalse(window.brand_icon.pixmap().isNull())
                self.assertGreaterEqual(window.navigation_scroll.height(), 32)
                self.assertFalse(window.windowIcon().isNull())
                window._show_tasks()
                # The brand mark never changes; only the section label beside it does.
                self.assertEqual(window.brand_label.text(), "Relay")
                self.assertEqual(window.page_title_label.text(), "Tasks")
            finally:
                window.close()


class AppIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_app_icon_renders_at_every_common_size(self):
        icon = app_icon()
        self.assertFalse(icon.isNull())
        for size in (16, 32, 48, 256):
            pixmap = icon.pixmap(size, size)
            self.assertFalse(pixmap.isNull())
            self.assertEqual(pixmap.width(), size)
            self.assertEqual(pixmap.height(), size)

    def test_app_icon_is_cached_across_calls(self):
        self.assertIs(app_icon(), app_icon())


if __name__ == "__main__":
    unittest.main()
