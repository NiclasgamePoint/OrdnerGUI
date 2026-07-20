import unittest

from PySide6.QtGui import QPalette

from app.gui.theme import ThemeManager, build_palette, build_stylesheet


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
            "#eaf0ef",
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.Base).name(),
            "#f5f8f7",
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
        self.assertIn(
            f"font-size: {ThemeManager.MAX_FONT_SIZE}px",
            build_stylesheet("dark", "#2db89d", 100, 99),
        )


if __name__ == "__main__":
    unittest.main()
