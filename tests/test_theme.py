import re
import unittest

from PySide6.QtGui import QPalette

from app.gui.theme import (
    FONT_FAMILY_FALLBACKS,
    ThemeManager,
    build_palette,
    build_stylesheet,
)


class ThemeTests(unittest.TestCase):
    def test_dark_palette_covers_native_widget_backgrounds(self):
        palette = build_palette("dark", "#f08a3c")
        self.assertEqual(
            palette.color(QPalette.ColorRole.Window).name(),
            "#11161b",
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.Base).name(),
            "#1a222a",
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.Text).name(),
            "#e9f1f8",
        )

    def test_light_palette_is_restored_after_dark_mode(self):
        palette = build_palette("light", "#2db89d")
        self.assertEqual(
            palette.color(QPalette.ColorRole.Window).name(),
            "#ddeaf4",
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.Base).name(),
            "#eef4fa",
        )
        self.assertIn("QHeaderView::section", build_stylesheet("light", "#2db89d"))
        self.assertIn("QScrollArea#PageScrollArea", build_stylesheet("dark", "#2db89d"))

    def test_contrast_changes_the_global_palette(self):
        normal = build_palette("light", "#2db89d", 100)
        strong = build_palette("light", "#2db89d", 140)
        self.assertNotEqual(
            normal.color(QPalette.ColorRole.Window).name(),
            strong.color(QPalette.ColorRole.Window).name(),
        )

    def test_font_size_is_applied_globally_and_bounded(self):
        stylesheet = build_stylesheet("dark", "#2db89d", 100, 18)
        self.assertIn("font-size: 18px", stylesheet)
        self.assertIn("QSlider#AppearanceSlider::groove:horizontal", stylesheet)
        self.assertIn("QLabel#SliderValue", stylesheet)
        self.assertIn(
            f"font-size: {ThemeManager.MAX_FONT_SIZE}px",
            build_stylesheet("dark", "#2db89d", 100, 99),
        )

    def test_font_fallbacks_and_minimum_size_are_cross_platform_safe(self):
        stylesheet = build_stylesheet("light", "#2db89d", 100, 1)
        self.assertIn(f"font-family: {FONT_FAMILY_FALLBACKS}", stylesheet)
        self.assertIn("'Segoe UI'", FONT_FAMILY_FALLBACKS)
        self.assertIn("'SF Pro Text'", FONT_FAMILY_FALLBACKS)
        self.assertIn("'Noto Sans'", FONT_FAMILY_FALLBACKS)
        self.assertTrue(FONT_FAMILY_FALLBACKS.endswith("sans-serif"))
        sizes = [
            int(value)
            for value in re.findall(r"font-size:\s*(\d+)px", stylesheet)
        ]
        self.assertTrue(sizes)
        self.assertGreaterEqual(min(sizes), ThemeManager.MIN_FONT_SIZE)


if __name__ == "__main__":
    unittest.main()
