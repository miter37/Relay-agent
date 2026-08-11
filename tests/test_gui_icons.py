"""Offscreen regression tests for Relay's icon system and icon-action widgets."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:  # pragma: no cover - CI without GUI extra
    raise unittest.SkipTest(f"GUI extra is not installed: {exc}") from exc

from relay.gui.design_icons import ICON_PATHS, icon
from relay.gui.design_widgets import IconButton, LabeledButton, NavButton


class IconSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_declared_icon_renders_a_non_empty_pixmap(self):
        for name in ICON_PATHS:
            result = icon(name)
            pixmap = result.pixmap(16, 16)
            self.assertFalse(pixmap.isNull(), f"icon {name!r} rendered a null pixmap")

    def test_unknown_icon_name_raises_key_error(self):
        with self.assertRaises(KeyError):
            icon("does-not-exist")

    def test_icon_button_always_has_tooltip_and_accessible_name(self):
        button = IconButton("refresh", "Refresh the Task list")

        self.assertEqual(button.objectName(), "iconAction")
        self.assertEqual(button.toolTip(), "Refresh the Task list")
        self.assertEqual(button.accessibleName(), "Refresh the Task list")
        self.assertFalse(button.icon().isNull())

    def test_icon_button_tone_is_exposed_as_a_qss_property(self):
        button = IconButton("trash", "Delete this Task", tone="danger")
        self.assertEqual(button.property("tone"), "danger")

    def test_nav_button_is_checkable_sidebar_button(self):
        nav = NavButton("list", "Runs")
        self.assertEqual(nav.objectName(), "sidebarButton")
        self.assertTrue(nav.isCheckable())
        self.assertEqual(nav.text(), "Runs")

    def test_labeled_button_primary_tone_uses_primary_action_object_name(self):
        primary = LabeledButton("plus", "Register Task", tone="primary")
        secondary = LabeledButton("plus", "Add files", tone="secondary")
        self.assertEqual(primary.objectName(), "primaryAction")
        self.assertNotEqual(secondary.objectName(), "primaryAction")


if __name__ == "__main__":
    unittest.main()
