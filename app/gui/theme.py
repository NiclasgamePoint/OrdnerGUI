from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette

from app.core.config import SETTINGS_APP, SETTINGS_ORG

FONT_FAMILY_FALLBACKS = (
    "'Segoe UI', 'SF Pro Text', 'Noto Sans', 'Ubuntu', sans-serif"
)


def _safe_color(value: str, fallback: str) -> str:
    color = QColor(value)
    if not color.isValid():
        return fallback
    return color.name()


def _tint(color_hex: str, lighter: bool, amount: int) -> str:
    color = QColor(color_hex)
    if not color.isValid():
        return color_hex
    if lighter:
        return color.lighter(amount).name()
    return color.darker(amount).name()


class ThemeManager:
    """Centralized theme handling with persisted mode/accent settings."""

    DEFAULT_MODE = "light"
    DEFAULT_ACCENT = "#2db89d"
    DEFAULT_CONTRAST = 100
    DEFAULT_FONT_SIZE = 13
    MIN_CONTRAST = 70
    MAX_CONTRAST = 140
    MIN_FONT_SIZE = 10
    MAX_FONT_SIZE = 20

    def __init__(self):
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.mode = self.DEFAULT_MODE
        self.accent = self.DEFAULT_ACCENT
        self.contrast = self.DEFAULT_CONTRAST
        self.font_size = self.DEFAULT_FONT_SIZE
        self.load()

    def load(self):
        mode = str(self.settings.value("theme_mode", self.DEFAULT_MODE)).strip().lower()
        accent = str(self.settings.value("accent_color", self.DEFAULT_ACCENT)).strip()
        contrast = _bounded_int(
            self.settings.value("contrast", self.DEFAULT_CONTRAST),
            self.DEFAULT_CONTRAST,
            self.MIN_CONTRAST,
            self.MAX_CONTRAST,
        )
        font_size = _bounded_int(
            self.settings.value("font_size", self.DEFAULT_FONT_SIZE),
            self.DEFAULT_FONT_SIZE,
            self.MIN_FONT_SIZE,
            self.MAX_FONT_SIZE,
        )

        if mode not in {"light", "dark"}:
            mode = self.DEFAULT_MODE

        self.mode = mode
        self.accent = _safe_color(accent, self.DEFAULT_ACCENT)
        self.contrast = contrast
        self.font_size = font_size

    def save(self):
        self.settings.setValue("theme_mode", self.mode)
        self.settings.setValue("accent_color", self.accent)
        self.settings.setValue("contrast", self.contrast)
        self.settings.setValue("font_size", self.font_size)
        self.settings.sync()

    def set_mode(self, mode: str):
        normalized = mode.strip().lower()
        if normalized in {"light", "dark"}:
            self.mode = normalized

    def set_accent(self, color_hex: str):
        self.accent = _safe_color(color_hex, self.DEFAULT_ACCENT)

    def set_contrast(self, contrast: int):
        self.contrast = max(self.MIN_CONTRAST, min(self.MAX_CONTRAST, int(contrast)))

    def set_font_size(self, font_size: int):
        self.font_size = max(self.MIN_FONT_SIZE, min(self.MAX_FONT_SIZE, int(font_size)))

    def apply(self, app):
        app.setStyleSheet("")
        app.setPalette(build_palette(self.mode, self.accent, self.contrast))
        app.setStyleSheet(
            build_stylesheet(self.mode, self.accent, self.contrast, self.font_size)
        )


def _bounded_int(value, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def _apply_contrast(color_hex: str, contrast: int) -> str:
    """Scale RGB distance from neutral gray while retaining valid colors."""
    color = QColor(color_hex)
    factor = max(0.0, contrast / 100.0)
    channels = [color.red(), color.green(), color.blue()]
    adjusted = [max(0, min(255, round(127.5 + (value - 127.5) * factor))) for value in channels]
    return QColor(*adjusted).name()


def build_palette(mode: str, accent: str, contrast: int = ThemeManager.DEFAULT_CONTRAST) -> QPalette:
    """Keep native and otherwise unstyled Qt controls aligned with the theme."""
    dark = mode.lower() == "dark"
    accent = _safe_color(accent, ThemeManager.DEFAULT_ACCENT)
    colors = {
        "window": "#11161b" if dark else "#eaf0ef",
        "base": "#1a222a" if dark else "#f5f8f7",
        "alternate": "#22303b" if dark else "#f0f6f5",
        "button": "#202b35" if dark else "#ffffff",
        "text": "#e9f1f8" if dark else "#1d2a34",
        "muted": "#9fb3c5" if dark else "#667b88",
    }
    colors = {name: _apply_contrast(value, contrast) for name, value in colors.items()}
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["alternate"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["button"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["muted"]))
    palette.setColor(QPalette.ColorRole.Link, QColor(accent))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(colors["muted"]),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(colors["muted"]),
    )
    return palette


def build_stylesheet(
    mode: str,
    accent: str,
    contrast: int = ThemeManager.DEFAULT_CONTRAST,
    font_size: int = ThemeManager.DEFAULT_FONT_SIZE,
) -> str:
    mode = mode.lower()
    accent = _safe_color(accent, ThemeManager.DEFAULT_ACCENT)
    accent_soft = _tint(accent, lighter=(mode == "dark"), amount=130 if mode == "dark" else 165)
    accent_hover = _tint(accent, lighter=(mode == "dark"), amount=112 if mode == "dark" else 108)
    accent_pressed = _tint(accent, lighter=False, amount=118)
    contrast = _bounded_int(
        contrast, ThemeManager.DEFAULT_CONTRAST,
        ThemeManager.MIN_CONTRAST, ThemeManager.MAX_CONTRAST,
    )
    font_size = _bounded_int(
        font_size, ThemeManager.DEFAULT_FONT_SIZE,
        ThemeManager.MIN_FONT_SIZE, ThemeManager.MAX_FONT_SIZE,
    )

    if mode == "dark":
        bg = "#11161b"
        surface = "#1a222a"
        card = "#202b35"
        text = "#e9f1f8"
        muted = "#9fb3c5"
        border = "#30404d"
        item_hover = "#263641"
        item_selected = accent_soft
        line = "#2a3945"
        chip = "#22303b"
    else:
        bg = "#eaf0ef"
        surface = "#f5f8f7"
        card = "#ffffff"
        text = "#1d2a34"
        muted = "#667b88"
        border = "#d7e3e1"
        item_hover = "#edf5f3"
        item_selected = accent_soft
        line = "#dce8e6"
        chip = "#f0f6f5"

    bg, surface, card, text, muted, border, item_hover, item_selected, line, chip = (
        _apply_contrast(color, contrast)
        for color in (bg, surface, card, text, muted, border, item_hover, item_selected, line, chip)
    )
    def scaled(size: int) -> int:
        return max(ThemeManager.MIN_FONT_SIZE, round(
            size * font_size / ThemeManager.DEFAULT_FONT_SIZE
        ))

    return f"""
    QWidget {{
        font-family: {FONT_FAMILY_FALLBACKS};
        font-size: {font_size}px;
    }}

    QMainWindow, QWidget#RootWidget {{
        background-color: {bg};
        color: {text};
        font-family: {FONT_FAMILY_FALLBACKS};
        font-size: {font_size}px;
    }}

    QWidget#TopBar,
    QWidget#SearchCard,
    QWidget#SideCard,
    QWidget#DetailsCard,
    QWidget#StatusCard,
    QWidget#AppHeader,
    QWidget#PageCard,
    QWidget#IndexStatusBar {{
        background-color: {card};
        border: 1px solid {border};
        border-radius: 14px;
    }}

    QStackedWidget#PageStack,
    QStackedWidget#ViewerStack,
    QWidget#SearchPage,
    QWidget#CustomerPage,
    QWidget#FolderPage,
    QWidget#SettingsPage,
    QWidget#DialogPage {{
        background-color: transparent;
        color: {text};
        border: none;
    }}

    QWidget#AppHeader {{
        border-radius: 13px;
    }}

    QWidget#IndexStatusBar {{
        border-radius: 10px;
    }}

    QLineEdit#GlobalSearchInput {{
        border-radius: 17px;
        padding-left: 13px;
        font-size: {scaled(14)}px;
    }}

    QFrame#ResultRow {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 11px;
    }}

    QFrame#ResultRow:hover {{
        background-color: {item_hover};
        border-color: {accent};
    }}

    QLabel#ResultTitle {{
        color: {text};
        font-size: {scaled(13)}px;
        font-weight: 700;
        border: none;
        background: transparent;
    }}

    QLabel#ResultSubtitle,
    QLabel#SearchSectionMessage {{
        color: {muted};
        font-size: {scaled(12)}px;
        border: none;
        background: transparent;
    }}

    QLabel#SearchSectionTitle {{
        color: {text};
        font-size: {scaled(13)}px;
        font-weight: 700;
        padding: 2px 3px;
        border: none;
        background: transparent;
    }}

    QLabel#CustomerValue {{
        color: {text};
        font-weight: 600;
        border: none;
        background: transparent;
    }}

    QLabel#IndexStatusText {{
        color: {muted};
        font-size: {scaled(12)}px;
        border: none;
        background: transparent;
    }}

    QScrollArea#SearchResultsScroll,
    QScrollArea#SearchResultsScroll QWidget#qt_scrollarea_viewport,
    QScrollArea#PageScrollArea,
    QScrollArea#PageScrollArea QWidget#qt_scrollarea_viewport,
    QWidget#SearchResultsContent,
    QWidget#ThemedScrollContent {{
        background-color: {card};
        color: {text};
        border: none;
    }}

    QListWidget#FolderFileList::item {{
        min-height: 42px;
        padding: 8px 10px;
    }}

    QWidget#CustomerDetailsSection,
    QWidget#FileViewerSection {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 12px;
    }}

    QLabel#SectionTitle {{
        color: {text};
        font-size: {scaled(14)}px;
        font-weight: 700;
        border: none;
        background: transparent;
    }}

    QLabel#PageTitle {{
        font-size: {scaled(15)}px;
        font-weight: 700;
        color: {text};
        border: none;
        background: transparent;
    }}

    QLabel#PageSubtitle {{
        font-size: {scaled(12)}px;
        color: {muted};
        border: none;
        background: transparent;
    }}

    QLabel#StatValue {{
        font-size: {scaled(16)}px;
        font-weight: 700;
        color: {text};
        border: none;
        background: transparent;
    }}

    QLabel#StatCaption {{
        font-size: {scaled(11)}px;
        color: {muted};
        border: none;
        background: transparent;
    }}

    QLabel#CustomerSummary {{
        color: {text};
        background-color: {chip};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 8px;
    }}

    QToolButton#SettingsButton {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 3px;
        color: {text};
        font-size: {scaled(17)}px;
    }}
    QToolButton#SettingsButton:hover {{
        border-color: {accent};
        background-color: {item_hover};
    }}

    QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit, QListWidget, QTreeWidget, QTableWidget, QTabWidget::pane {{
        background-color: {surface};
        color: {text};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 7px;
        selection-background-color: {accent};
        selection-color: #ffffff;
    }}

    QLineEdit {{
        placeholder-text-color: {muted};
    }}

    QPlainTextEdit:read-only, QTextEdit:read-only {{
        background-color: {surface};
        color: {text};
    }}

    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}

    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 6px solid {muted};
        margin-right: 6px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {surface};
        color: {text};
        border: 1px solid {border};
        border-radius: 8px;
        selection-background-color: {item_selected};
        selection-color: #ffffff;
        outline: 0;
        padding: 5px;
    }}

    QComboBox QAbstractItemView::item {{
        min-height: 28px;
        padding: 3px 8px;
        border-radius: 6px;
    }}

    QComboBox:disabled {{
        color: {muted};
        background-color: {chip};
    }}

    QAbstractItemView {{
        background-color: {surface};
        color: {text};
        alternate-background-color: {chip};
        selection-background-color: {item_selected};
        selection-color: #ffffff;
        outline: 0;
    }}

    QTableWidget {{
        gridline-color: {border};
    }}

    QTableWidget::item {{
        padding: 5px;
    }}

    QHeaderView {{
        background-color: {chip};
        color: {text};
        border: none;
    }}

    QHeaderView::section {{
        background-color: {chip};
        color: {text};
        border: none;
        border-right: 1px solid {border};
        border-bottom: 1px solid {border};
        padding: 6px 8px;
        font-weight: 600;
    }}

    QTableCornerButton::section {{
        background-color: {chip};
        border: none;
        border-right: 1px solid {border};
        border-bottom: 1px solid {border};
    }}

    QMenu {{
        background-color: {surface};
        color: {text};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 4px;
    }}

    QMenu::item {{
        padding: 6px 10px;
        border-radius: 6px;
    }}

    QMenu::item:selected {{
        background-color: {item_selected};
        color: {text};
    }}

    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {{
        border-color: {accent};
    }}

    QSlider#AppearanceSlider {{
        min-height: 24px;
        background: transparent;
    }}

    QSlider#AppearanceSlider::groove:horizontal {{
        height: 6px;
        background-color: {border};
        border-radius: 3px;
    }}

    QSlider#AppearanceSlider::sub-page:horizontal {{
        background-color: {accent};
        border-radius: 3px;
    }}

    QSlider#AppearanceSlider::handle:horizontal {{
        width: 18px;
        margin: -6px 0;
        background-color: {card};
        border: 2px solid {accent};
        border-radius: 9px;
    }}

    QSlider#AppearanceSlider::handle:horizontal:hover {{
        background-color: {accent_soft};
    }}

    QLabel#SliderValue {{
        color: {text};
        font-weight: 600;
        border: none;
        background: transparent;
    }}

    QSpinBox::up-button, QSpinBox::down-button {{
        background-color: {chip};
        border: none;
        width: 18px;
    }}

    QCheckBox {{
        color: {text};
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 4px;
    }}

    QCheckBox::indicator:checked {{
        background-color: {accent};
        border-color: {accent};
    }}

    QListWidget::item, QTreeWidget::item {{
        padding: 8px;
        margin: 1px;
        border-radius: 8px;
    }}
    QListWidget::item:hover, QTreeWidget::item:hover {{
        background-color: {item_hover};
    }}
    QTableWidget::item:hover {{
        background-color: {item_hover};
    }}
    QListWidget::item:selected, QTreeWidget::item:selected {{
        background-color: {item_selected};
        color: #ffffff;
    }}

    QTreeWidget#ResultsTree {{
        padding: 5px;
    }}

    QTreeWidget#ResultsTree::item:disabled {{
        color: {muted};
    }}

    QPushButton {{
        background-color: {accent};
        color: #ffffff;
        border: none;
        border-radius: 10px;
        padding: 8px 14px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {accent_hover};
    }}
    QPushButton:pressed {{
        background-color: {accent_pressed};
    }}

    QPushButton:disabled {{
        background-color: {border};
        color: {muted};
    }}

    QPushButton[buttonRole="secondary"] {{
        background-color: {chip};
        color: {text};
        border: 1px solid {border};
    }}

    QPushButton[buttonRole="secondary"]:hover {{
        background-color: {item_hover};
        border-color: {accent};
    }}

    QPushButton[buttonRole="secondary"][filtersActive="true"] {{
        background-color: {item_selected};
        color: #ffffff;
        border-color: {accent};
    }}

    QPushButton[buttonRole="secondary"]:disabled {{
        background-color: {surface};
        color: {muted};
        border-color: {border};
    }}

    QPushButton[hasBadge="true"] {{
        padding-right: 42px;
    }}

    QLabel#ButtonCountBadge {{
        background-color: {item_selected};
        color: #ffffff;
        border: none;
        border-radius: 12px;
        font-weight: 700;
    }}

    QPushButton[buttonRole="danger"] {{
        background-color: #c84d4d;
        color: #ffffff;
    }}

    QLabel#BusyIndicator {{
        color: {accent};
        background: transparent;
        border: none;
        font-size: {scaled(18)}px;
        font-weight: 700;
    }}

    QPushButton#GhostButton {{
        background-color: {chip};
        color: {text};
        border: 1px solid {border};
    }}
    QPushButton#GhostButton:hover {{
        border-color: {accent};
    }}

    QProgressBar {{
        border: 1px solid {border};
        border-radius: 8px;
        background-color: {surface};
        color: {text};
        text-align: center;
    }}

    QProgressBar::chunk {{
        background-color: {accent};
        border-radius: 6px;
    }}

    QTabBar::tab {{
        background-color: {surface};
        color: {muted};
        border: 1px solid {border};
        border-bottom: none;
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        padding: 8px 12px;
        margin-right: 4px;
    }}
    QTabBar::tab:selected {{
        color: {text};
        background-color: {card};
        border-color: {accent};
    }}

    QSplitter::handle {{
        background-color: {line};
        width: 2px;
    }}

    QSplitter#DetailsSplitter::handle {{
        background-color: transparent;
        width: 10px;
    }}

    QScrollArea {{
        border: none;
        background: transparent;
        color: {text};
    }}

    QScrollArea QWidget#qt_scrollarea_viewport {{
        background-color: transparent;
        color: {text};
    }}

    QWidget#FileViewer,
    QWidget#ViewerContent,
    QStackedWidget#ViewerStack,
    QScrollArea#ImageViewerScroll,
    QScrollArea#ImageViewerScroll QWidget#qt_scrollarea_viewport,
    QScrollArea#ViewerScrollArea,
    QScrollArea#ViewerScrollArea QWidget#qt_scrollarea_viewport {{
        background-color: {surface};
        color: {text};
        border: none;
    }}

    QWidget#FileViewer QLabel,
    QWidget#ViewerContent QLabel {{
        background: transparent;
        color: {text};
        border: none;
    }}

    QLabel#ViewerFileInfo {{
        color: {text};
        font-size: {scaled(16)}px;
        font-weight: 700;
        background: transparent;
        border: none;
    }}

    QLabel#ViewerMeta {{
        color: {muted};
        font-size: {scaled(12)}px;
        background: transparent;
        border: none;
    }}

    QWidget#ViewerLoading {{
        background-color: {surface};
        color: {text};
        border: none;
    }}

    QLabel#ViewerLoadingText {{
        color: {muted};
        font-size: {scaled(13)}px;
        background: transparent;
        border: none;
    }}

    QPdfView#PdfView,
    QPdfView#PdfView QWidget#qt_scrollarea_viewport {{
        background-color: {surface};
        color: {text};
        border: 1px solid {border};
        border-radius: 8px;
    }}

    QScrollBar:vertical {{
        background-color: {surface};
        width: 12px;
        margin: 2px;
        border: none;
        border-radius: 6px;
    }}

    QScrollBar::handle:vertical {{
        background-color: {border};
        min-height: 28px;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: {accent};
    }}

    QScrollBar:horizontal {{
        background-color: {surface};
        height: 12px;
        margin: 2px;
        border: none;
        border-radius: 6px;
    }}

    QScrollBar::handle:horizontal {{
        background-color: {border};
        min-width: 28px;
        border-radius: 4px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background-color: {accent};
    }}

    QScrollBar::add-line,
    QScrollBar::sub-line {{
        width: 0;
        height: 0;
        border: none;
        background: none;
    }}

    QScrollBar::add-page,
    QScrollBar::sub-page {{
        background: none;
    }}

    QAbstractScrollArea::corner {{
        background-color: {surface};
        border: none;
    }}

    QToolTip {{
        background-color: {card};
        color: {text};
        border: 1px solid {border};
        padding: 5px;
    }}

    QDialog {{
        background-color: {card};
        color: {text};
    }}

    QDialog#CenteredPopupDialog {{
        background-color: transparent;
        color: {text};
    }}

    QFrame#CustomerEditorBody {{
        background-color: {card};
        color: {text};
        border: 1px solid {border};
        border-radius: 14px;
    }}

    QFrame#RecognitionReviewBody {{
        background-color: {card};
        color: {text};
        border: 1px solid {border};
        border-radius: 14px;
    }}

    QFrame#ExistingCustomerAssignment {{
        background-color: {chip};
        color: {text};
        border: 1px solid {border};
        border-radius: 10px;
    }}

    QFrame#ExistingCustomerAssignment QLabel {{
        border: none;
        background-color: transparent;
    }}

    QFrame#SettingsPopup {{
        background-color: transparent;
    }}

    QFrame#SearchFilterPopup {{
        background-color: transparent;
    }}

    QFrame#SearchFilterBody {{
        background-color: {card};
        border: 1px solid {border};
        border-radius: 12px;
    }}

    QFrame#SettingsBody {{
        background-color: {card};
        border: 1px solid {border};
        border-radius: 12px;
    }}

    QLabel#PopupTitle {{
        font-size: {scaled(13)}px;
        font-weight: 700;
        color: {text};
        border: none;
        background: transparent;
    }}

    QLabel#PopupCaption {{
        color: {muted};
        border: none;
        background: transparent;
        min-width: 46px;
    }}

    QLabel#SettingsError {{
        color: #d84a4a;
        border: none;
        background: transparent;
    }}

    QLabel#PopupSectionTitle {{
        font-size: {scaled(14)}px;
        font-weight: 700;
        color: {text};
        border: none;
        background: transparent;
    }}

    QLabel#SearchGroupTitle {{
        color: {text};
        font-size: {scaled(12)}px;
        font-weight: 700;
        padding: 2px 4px;
        border: none;
        background: transparent;
    }}

    QSplitter#ResultsSplitter::handle {{
        background-color: transparent;
        height: 8px;
    }}

    QListWidget#SettingsNav {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 6px;
    }}

    QListWidget#SettingsNav::item {{
        padding: 0 12px;
        margin: 1px 0;
        border-radius: 8px;
    }}

    QListWidget#SettingsNav::item:hover {{
        background-color: {item_hover};
    }}

    QListWidget#SettingsNav::item:selected {{
        background-color: {item_selected};
        color: #ffffff;
        font-weight: 600;
    }}
    """
