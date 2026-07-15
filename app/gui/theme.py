from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor


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

    SETTINGS_ORG = "PapaGUI"
    SETTINGS_APP = "UI"
    DEFAULT_MODE = "light"
    DEFAULT_ACCENT = "#2db89d"

    def __init__(self):
        self.settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self.mode = self.DEFAULT_MODE
        self.accent = self.DEFAULT_ACCENT
        self.load()

    def load(self):
        mode = str(self.settings.value("theme_mode", self.DEFAULT_MODE)).strip().lower()
        accent = str(self.settings.value("accent_color", self.DEFAULT_ACCENT)).strip()

        if mode not in {"light", "dark"}:
            mode = self.DEFAULT_MODE

        self.mode = mode
        self.accent = _safe_color(accent, self.DEFAULT_ACCENT)

    def save(self):
        self.settings.setValue("theme_mode", self.mode)
        self.settings.setValue("accent_color", self.accent)

    def set_mode(self, mode: str):
        normalized = mode.strip().lower()
        if normalized in {"light", "dark"}:
            self.mode = normalized

    def set_accent(self, color_hex: str):
        self.accent = _safe_color(color_hex, self.DEFAULT_ACCENT)

    def apply(self, app):
        app.setStyleSheet(build_stylesheet(self.mode, self.accent))


def build_stylesheet(mode: str, accent: str) -> str:
    mode = mode.lower()
    accent = _safe_color(accent, ThemeManager.DEFAULT_ACCENT)
    accent_soft = _tint(accent, lighter=(mode == "dark"), amount=130 if mode == "dark" else 165)
    accent_hover = _tint(accent, lighter=(mode == "dark"), amount=112 if mode == "dark" else 108)
    accent_pressed = _tint(accent, lighter=False, amount=118)

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

    return f"""
    QMainWindow, QWidget#RootWidget {{
        background-color: {bg};
        color: {text};
        font-family: 'Segoe UI', 'Noto Sans', sans-serif;
        font-size: 13px;
    }}

    QWidget#TopBar,
    QWidget#SearchCard,
    QWidget#SideCard,
    QWidget#DetailsCard,
    QWidget#StatusCard {{
        background-color: {card};
        border: 1px solid {border};
        border-radius: 14px;
    }}

    QWidget#CustomerDetailsSection,
    QWidget#FileViewerSection {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 12px;
    }}

    QLabel#SectionTitle {{
        color: {text};
        font-size: 14px;
        font-weight: 700;
        border: none;
        background: transparent;
    }}

    QLabel#PageTitle {{
        font-size: 15px;
        font-weight: 700;
        color: {text};
        border: none;
        background: transparent;
    }}

    QLabel#PageSubtitle {{
        font-size: 12px;
        color: {muted};
        border: none;
        background: transparent;
    }}

    QLabel#StatValue {{
        font-size: 16px;
        font-weight: 700;
        color: {text};
        border: none;
        background: transparent;
    }}

    QLabel#StatCaption {{
        font-size: 11px;
        color: {muted};
        border: none;
        background: transparent;
    }}

    QToolButton#SettingsButton {{
        background-color: {surface};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 3px;
        color: {text};
        font-size: 17px;
    }}
    QToolButton#SettingsButton:hover {{
        border-color: {accent};
        background-color: {item_hover};
    }}

    QLineEdit, QComboBox, QSpinBox, QTextEdit, QListWidget, QTreeWidget, QTableWidget, QTabWidget::pane {{
        background-color: {surface};
        color: {text};
        border: 1px solid {border};
        border-radius: 10px;
        padding: 7px;
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

    QPushButton[buttonRole="secondary"]:disabled {{
        background-color: {surface};
        color: {muted};
        border-color: {border};
    }}

    QPushButton[buttonRole="danger"] {{
        background-color: #c84d4d;
        color: #ffffff;
    }}

    QLabel#BusyIndicator {{
        color: {accent};
        background: transparent;
        border: none;
        font-size: 18px;
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
    }}

    QWidget#FileViewer,
    QWidget#ViewerContent,
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
        font-size: 16px;
        font-weight: 700;
        background: transparent;
        border: none;
    }}

    QLabel#ViewerMeta {{
        color: {muted};
        font-size: 12px;
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

    QDialog {{
        background-color: {card};
        color: {text};
    }}

    QFrame#SettingsPopup {{
        background-color: transparent;
    }}

    QFrame#SettingsBody {{
        background-color: {card};
        border: 1px solid {border};
        border-radius: 12px;
    }}

    QLabel#PopupTitle {{
        font-size: 13px;
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
        font-size: 14px;
        font-weight: 700;
        color: {text};
        border: none;
        background: transparent;
    }}

    QLabel#SearchGroupTitle {{
        color: {text};
        font-size: 12px;
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
