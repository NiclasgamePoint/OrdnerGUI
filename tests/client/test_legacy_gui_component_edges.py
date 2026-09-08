"""Behavioral edge coverage for the restored v0.4.1 presentation building blocks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QTreeWidgetItem,
    QWidget,
)

from papagui_contracts import Customer, CustomerProject, SourcePath
from papagui_client.gui import legacy_models
from papagui_client.gui.dialogs.centered_popup import CenteredPopupDialog
from papagui_client.gui.legacy_models import (
    ApplicationStatistics,
    SearchFilters,
    SearchPreferences,
    SearchSort,
)
from papagui_client.gui.navigation import NavigationController, NavigationEntry
from papagui_client.gui.pages.folder_page import FolderPage
from papagui_client.gui.pages.search_page import SearchPage, _ResultSection
from papagui_client.gui.panels.mail_panel import MailPanel
from papagui_client.gui.settings_help import SettingsHelpBubble, SettingsHelpController
from papagui_client.gui.theme import (
    ThemeManager,
    _apply_contrast,
    _bounded_int,
    _safe_color,
    _tint,
    build_palette,
    build_stylesheet,
)
from papagui_client.gui.widgets.app_header import AppHeader
from papagui_client.gui.widgets.buttons import AppButton, BusyIndicator, CountBadgeButton
from papagui_client.gui.widgets.document_result_row import DocumentResultRow
from papagui_client.gui.widgets.index_status_bar import IndexStatusBar
from papagui_client.gui.widgets.result_row import ResultRow
from papagui_client.gui.widgets.search_filter_popup import SearchFilterPopup
from papagui_client.gui.widgets.statistics_widget import StatisticsWidget


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def process_qt_events(application):
    yield
    application.processEvents()


class MemorySettings:
    values: dict[str, object] = {}

    def __init__(self, *_args):
        pass

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.values["synced"] = True


def _mouse_event(
    button: Qt.MouseButton,
    position: QPointF,
) -> QMouseEvent:
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        position,
        position,
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def test_legacy_models_preferences_validate_and_persist(monkeypatch):
    assert not SearchFilters().active
    assert SearchFilters(domain_folder="Planung").active
    assert SearchFilters(year="2026").active
    assert SearchFilters(file_type="pdf").active

    MemorySettings.values = {
        SearchPreferences.SORT_KEY: SearchSort.DATE.value,
        SearchPreferences.SUBFOLDERS_KEY: True,
    }
    monkeypatch.setattr(legacy_models, "QSettings", MemorySettings)
    preferences = SearchPreferences()
    assert preferences.load() == (SearchSort.DATE, True)

    MemorySettings.values[SearchPreferences.SUBFOLDERS_KEY] = "YES"
    assert preferences.load() == (SearchSort.DATE, True)
    MemorySettings.values[SearchPreferences.SUBFOLDERS_KEY] = "no"
    assert preferences.load() == (SearchSort.DATE, False)
    MemorySettings.values[SearchPreferences.SORT_KEY] = "not-a-sort"
    assert preferences.load() == (SearchSort.RELEVANCE, False)

    preferences.save(SearchSort.ALPHABETICAL, True)
    assert MemorySettings.values[SearchPreferences.SORT_KEY] == "alphabetical"
    assert MemorySettings.values[SearchPreferences.SUBFOLDERS_KEY] is True
    preferences.save("invalid", False)
    assert MemorySettings.values[SearchPreferences.SORT_KEY] == "relevance"
    assert MemorySettings.values[SearchPreferences.SUBFOLDERS_KEY] is False


def test_theme_helpers_manager_and_both_palettes(application, monkeypatch):
    assert _safe_color("not-a-color", "#112233") == "#112233"
    assert _safe_color("#ABCDEF", "#000000") == "#abcdef"
    assert _tint("invalid", True, 120) == "invalid"
    assert QColor(_tint("#336699", True, 120)).isValid()
    assert QColor(_tint("#336699", False, 120)).isValid()
    assert _bounded_int("bad", 12, 10, 20) == 12
    assert _bounded_int(1, 12, 10, 20) == 10
    assert _bounded_int(99, 12, 10, 20) == 20
    assert _apply_contrast("#000000", 0) == "#808080"

    stylesheets = {}
    for mode in ("light", "dark"):
        palette = build_palette(mode, "invalid", 100)
        assert palette.color(palette.ColorRole.Highlight).name() == "#2db89d"
        stylesheet = build_stylesheet(mode.upper(), "#336699", 5, 99)
        stylesheets[mode] = stylesheet
        assert "QWidget#AppHeader" in stylesheet
        assert "font-size: 20px" in stylesheet
    assert stylesheets["light"] != stylesheets["dark"]

    MemorySettings.values = {
        "theme_mode": "sepia",
        "accent_color": "broken",
        "contrast": "broken",
        "font_size": 50,
    }
    monkeypatch.setattr("papagui_client.gui.theme.QSettings", MemorySettings)
    manager = ThemeManager()
    assert (manager.mode, manager.accent) == ("light", "#2db89d")
    assert manager.contrast == 100
    assert manager.font_size == 20

    manager.set_mode(" DARK ")
    manager.set_mode("unknown")
    manager.set_accent("broken")
    manager.set_contrast(1)
    manager.set_font_size(99)
    assert (manager.mode, manager.accent, manager.contrast, manager.font_size) == (
        "dark",
        "#2db89d",
        70,
        20,
    )
    manager.save()
    assert MemorySettings.values["synced"] is True
    manager.apply(application)
    assert "QDialog#IndexControlWindow" in application.styleSheet()

    MemorySettings.values = {
        "theme_mode": "dark",
        "accent_color": "#123456",
        "contrast": 110,
        "font_size": 14,
    }
    valid_manager = ThemeManager()
    assert (valid_manager.mode, valid_manager.accent) == ("dark", "#123456")


def test_navigation_history_duplicate_nonremembered_and_reset(application):
    controller = NavigationController()
    routes = []
    back_states = []
    forward_states = []
    controller.routeChanged.connect(routes.append)
    controller.canGoBackChanged.connect(back_states.append)
    controller.canGoForwardChanged.connect(forward_states.append)

    controller.back()
    controller.forward()
    controller.navigate("search")
    assert routes == [NavigationEntry("search")]
    assert not controller.can_go_back

    controller.navigate("folder", {"path": "a"})
    assert controller.can_go_back
    controller.navigate("customer", 7, remember=False)
    controller.back()
    assert controller.current == NavigationEntry("search")
    assert controller.can_go_forward
    controller.forward()
    assert controller.current == NavigationEntry("customer", 7)
    assert back_states[-1] is True
    assert forward_states[-1] is False

    controller.reset("folder", "root")
    assert controller.current == NavigationEntry("folder", "root")
    assert back_states[-1] is False
    assert forward_states[-1] is False


def test_help_bubble_positioning_with_and_without_screen(application, monkeypatch):
    anchor = QWidget()
    anchor.resize(100, 20)
    anchor.move(20, 30)
    anchor.show()
    application.processEvents()
    bubble = SettingsHelpBubble()

    class Screen:
        @staticmethod
        def availableGeometry():
            return QRect(0, 0, 500, 300)

    fake_gui = SimpleNamespace(
        screenAt=lambda _point: Screen(),
        primaryScreen=lambda: Screen(),
    )
    monkeypatch.setattr("papagui_client.gui.settings_help.QGuiApplication", fake_gui)
    bubble.show_for(anchor, "A rather long help text which must stay inside the screen.")
    assert bubble.isVisible()
    assert bubble.geometry().left() >= 8
    assert bubble.geometry().top() >= 8
    anchor.move(20, 260)
    bubble.show_for(anchor, "Above the anchor")
    assert bubble.geometry().bottom() <= 291

    fake_gui.screenAt = lambda _point: None
    fake_gui.primaryScreen = lambda: None
    bubble.show_for(anchor, "No screen fallback")
    assert bubble.pos() == anchor.mapToGlobal(QPoint(0, anchor.height())) + QPoint(0, 4)
    bubble.close()
    anchor.close()


def test_help_controller_registration_activation_suppression_and_disposal(
    application, monkeypatch
):
    owner = QWidget()
    root = QWidget(owner)
    child = QLabel("child", root)
    root.resize(120, 40)
    owner.resize(300, 200)
    owner.show()
    root.show()
    child.show()
    application.processEvents()
    controller = SettingsHelpController(owner, -4)
    assert controller.delay_ms == 0
    controller.set_delay_ms(-9)
    assert controller.delay_ms == 0
    with pytest.raises(ValueError):
        controller.register("", "Text", root)
    with pytest.raises(ValueError):
        controller.register("id", "", root)

    controller.register("server", "Server-Adresse", root)
    assert controller.registered_ids() == {"server"}
    assert child.property("settingsHelpId") == "server"
    assert not controller.eventFilter(QWidget(), QEvent(QEvent.Type.Enter))

    shown = []
    monkeypatch.setattr(controller.bubble, "show_for", lambda anchor, text: shown.append((anchor, text)))
    controller.eventFilter(child, QEvent(QEvent.Type.Enter))
    controller._show_pending()
    assert shown == [(root, "Server-Adresse")]
    controller._activate(root)  # already visible
    controller.eventFilter(child, QEvent(QEvent.Type.FocusIn))
    assert controller._is_active(root)
    controller._hide_if_inactive()
    assert controller._visible_id == "server"
    controller.eventFilter(child, QEvent(QEvent.Type.User))
    controller._watch(QObject(owner), root)

    controller.eventFilter(child, QEvent(QEvent.Type.MouseButtonPress))
    assert controller._suppressed_id == "server"
    controller._activate(root)  # suppressed until all tracked inputs are inactive
    assert not controller._timer.isActive()
    controller.eventFilter(child, QEvent(QEvent.Type.Leave))
    controller.eventFilter(child, QEvent(QEvent.Type.FocusOut))
    controller._hide_if_inactive()
    assert controller._suppressed_id == ""

    controller._pending_anchor = None
    controller._show_pending()
    controller.eventFilter(owner, QEvent(QEvent.Type.Hide))
    controller.hide()
    controller.dispose()
    controller.dispose()
    controller._show_pending()
    assert controller.bubble is None
    assert not controller.eventFilter(root, QEvent(QEvent.Type.Enter))
    owner.close()

    no_bubble_owner = QWidget()
    no_bubble_controller = SettingsHelpController(no_bubble_owner)
    orphaned_bubble = no_bubble_controller.bubble
    no_bubble_controller.bubble = None
    no_bubble_controller.hide()
    no_bubble_controller.dispose()
    orphaned_bubble.close()
    no_bubble_owner.close()


def test_help_controller_handles_deleted_watched_objects(application, monkeypatch):
    owner = QWidget()
    watched = QWidget(owner)
    controller = SettingsHelpController(owner)
    controller.register("deleted", "Deleted widget", watched)

    def deleted_filter(_controller):
        raise RuntimeError("C++ object deleted")

    monkeypatch.setattr(watched, "removeEventFilter", deleted_filter)
    monkeypatch.setattr(owner, "removeEventFilter", deleted_filter)
    controller.dispose()
    assert controller.bubble is None
    owner.close()


def test_buttons_header_busy_badge_and_filter_state(application, monkeypatch):
    button = AppButton("Aktion")
    no_width = AppButton("Ohne Breite", AppButton.SECONDARY, minimum_width=None)
    assert no_width.property("buttonRole") == AppButton.SECONDARY
    button.set_role(AppButton.DANGER)
    assert button.property("buttonRole") == AppButton.DANGER
    button.setEnabled(True)
    button.set_busy(False)
    button.set_busy(True)
    button.set_busy(True)
    assert not button.isEnabled()
    button.set_busy(False)
    assert button.isEnabled()
    button.setEnabled(False)
    button.set_busy(True)
    button.set_busy(False)
    assert not button.isEnabled()

    badge = CountBadgeButton("Prüffälle")
    badge.resize(160, 40)
    badge.set_count(-1)
    assert badge.count() == 0
    badge.set_count(12)
    assert badge.count() == 12
    assert badge.property("hasBadge") is True
    assert badge.badge.x() >= 8
    badge.resize(QSize(80, 20))
    badge.resizeEvent(QResizeEvent(QSize(80, 20), QSize(160, 40)))

    busy = BusyIndicator()
    busy.start()
    assert busy.is_running() and busy.text() == "◐"
    for _ in range(len(busy.FRAMES)):
        busy._advance()
    assert busy.text() == "◐"
    busy.stop()
    assert not busy.is_running() and not busy.text()

    null_header = AppHeader(["Alt"], document_search_enabled=False)
    assert "Kunden oder Ordner" in null_header.search_input.placeholderText()
    null_header.set_document_search_enabled(True)
    assert "Dokumentinhalte" in null_header.search_input.placeholderText()
    null_header.set_query("  Muster  ")
    assert null_header.query() == "Muster"
    null_header.set_history(["A", "B"])
    assert null_header.history_model.stringList() == ["A", "B"]
    null_header.set_filter_count(0)
    assert null_header.filter_button.text() == "Filter"
    null_header.set_filter_count(2)
    assert null_header.filter_button.text() == "Filter (2)"
    assert null_header.filter_button.property("filtersActive") is True

    requested = []
    null_header.searchRequested.connect(lambda: requested.append("search"))
    null_header.filterRequested.connect(lambda: requested.append("filter"))
    null_header.settingsRequested.connect(lambda: requested.append("settings"))
    null_header.search_input.returnPressed.emit()
    null_header.filter_button.click()
    null_header.settings_button.click()
    assert requested == ["search", "filter", "settings"]

    icon = QIcon(QPixmap(2, 2))
    monkeypatch.setattr(
        "papagui_client.gui.widgets.app_header.QIcon.fromTheme",
        lambda _name: icon,
    )
    icon_header = AppHeader(document_search_enabled=True)
    assert not icon_header.settings_button.icon().isNull()


@pytest.mark.parametrize("with_path", [False, True])
def test_result_rows_emit_click_and_path_actions(application, with_path):
    path = "/tmp/result/file.txt" if with_path else ""
    row = ResultRow("Titel", "Untertitel" if with_path else "", {"id": 1}, path)
    activated = []
    opened = []
    row.activated.connect(activated.append)
    row.openPathRequested.connect(opened.append)
    row.resize(300, 80)

    row._open_path()
    assert opened == ([path] if with_path else [])
    inside = _mouse_event(Qt.MouseButton.LeftButton, QPointF(10, 10))
    row.mouseReleaseEvent(inside)
    assert activated == [{"id": 1}]
    outside = _mouse_event(Qt.MouseButton.LeftButton, QPointF(-10, -10))
    row.mouseReleaseEvent(outside)
    right = _mouse_event(Qt.MouseButton.RightButton, QPointF(10, 10))
    row.mouseReleaseEvent(right)
    assert activated == [{"id": 1}]

    document = DocumentResultRow("Datei", "" if with_path else "Text", path)
    assert document.path == path
    files = []
    folders = []
    document.openFileRequested.connect(files.append)
    document.openPathRequested.connect(folders.append)
    document._open_file()
    document._open_folder()
    assert files == ([path] if with_path else [])
    assert folders == ([str(Path(path).parent)] if with_path else [])
    document.resize(300, 90)
    document.mouseReleaseEvent(_mouse_event(Qt.MouseButton.LeftButton, QPointF(5, 5)))
    assert len(files) == (2 if with_path else 0)
    document.mouseReleaseEvent(_mouse_event(Qt.MouseButton.RightButton, QPointF(5, 5)))
    document.mouseReleaseEvent(_mouse_event(Qt.MouseButton.LeftButton, QPointF(-5, -5)))


class FakeMenu:
    selection = ""

    def __init__(self, *_args):
        self.actions = []

    def addAction(self, label):
        action = SimpleNamespace(label=label, enabled=True)
        action.setEnabled = lambda enabled: setattr(action, "enabled", enabled)
        self.actions.append(action)
        return action

    def exec(self, _position):
        return next((action for action in self.actions if action.label == self.selection), None)


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("Oeffnen/Anzeigen", "activate"),
        ("Ordner im System oeffnen", "path"),
        ("Pfad kopieren", "copy"),
        ("", "none"),
    ],
)
def test_result_row_context_menu_choices(application, monkeypatch, selection, expected):
    FakeMenu.selection = selection
    monkeypatch.setattr("papagui_client.gui.widgets.result_row.QMenu", FakeMenu)
    row = ResultRow("Titel", "", 42, "/tmp/a")
    activated = []
    paths = []
    row.activated.connect(activated.append)
    row.openPathRequested.connect(paths.append)
    row.contextMenuEvent(SimpleNamespace(globalPos=lambda: QPoint(1, 1)))
    assert activated == ([42] if expected == "activate" else [])
    assert paths == (["/tmp/a"] if expected == "path" else [])
    if expected == "copy":
        assert QApplication.clipboard().text() == "/tmp/a"


def test_result_row_context_path_actions_are_disabled_without_path(application, monkeypatch):
    for selection in ("Ordner im System oeffnen", "Pfad kopieren"):
        FakeMenu.selection = selection
        monkeypatch.setattr("papagui_client.gui.widgets.result_row.QMenu", FakeMenu)
        row = ResultRow("Titel", "", 42, "")
        paths = []
        row.openPathRequested.connect(paths.append)
        row.contextMenuEvent(SimpleNamespace(globalPos=lambda: QPoint()))
        assert not paths


def test_index_status_bar_progress_and_mouse_actions(application):
    status = IndexStatusBar()
    texts = []
    busy_states = []
    details = []
    cancellations = []
    status.textChanged.connect(texts.append)
    status.busyChanged.connect(busy_states.append)
    status.detailsRequested.connect(lambda: details.append(True))
    status.cancelRequested.connect(lambda: cancellations.append(True))
    status.set_text("Synchronisiere")
    status.set_busy(True)
    assert status.progress_bar.maximum() == 0
    status.cancel_button.click()
    status.set_busy(False)
    assert status.progress_bar.maximum() == 100
    status.mouseReleaseEvent(_mouse_event(Qt.MouseButton.LeftButton, QPointF(4, 4)))
    status.mouseReleaseEvent(_mouse_event(Qt.MouseButton.RightButton, QPointF(4, 4)))
    assert texts == ["Synchronisiere"]
    assert busy_states == [True, False]
    assert details == [True]
    assert cancellations == [True]


def test_filter_popup_counts_emits_and_clears_all_dimensions(application):
    popup = SearchFilterPopup()
    for combo, value in zip(popup.combos, ("Planung", "2026", "pdf"), strict=True):
        combo.addItem("Alle", "")
        combo.addItem(value, value)
    emitted = []
    popup.filtersChanged.connect(lambda: emitted.append(True))
    assert popup.active_filter_count() == 0
    popup.clear_filters()
    assert not emitted

    for combo in popup.combos:
        combo.setCurrentIndex(1)
    popup.sort_combo.setCurrentIndex(popup.sort_combo.findData(SearchSort.DATE))
    popup.include_subfolders_checkbox.setChecked(True)
    assert popup.active_filter_count() == 5
    emissions_before_clear = len(emitted)
    popup.clear_button.click()
    assert len(emitted) == emissions_before_clear + 1
    assert popup.active_filter_count() == 0


@pytest.mark.parametrize(
    ("size", "formatted"),
    [
        (12, "12 B"),
        (1024, "1.0 KB"),
        (1024**2, "1.00 MB"),
        (1024**3, "1.00 GB"),
    ],
)
def test_statistics_size_and_timestamp_formatting(size, formatted):
    assert StatisticsWidget._format_size(size) == formatted
    assert StatisticsWidget._format_timestamp("") == "Noch nicht ausgeführt"
    assert StatisticsWidget._format_timestamp("invalid") == "invalid"
    assert StatisticsWidget._format_timestamp("2026-01-02T03:04:00Z") == "02.01.2026 03:04"


def test_statistics_widget_full_compact_loading_error_and_clear(application):
    statistics = ApplicationStatistics(
        customer_count=1,
        contact_count=2,
        project_count=3,
        service_count=4,
        file_count=5,
        total_file_size=2_048,
        content_count=6,
        pending_recognition_count=7,
        last_indexed_at="2026-01-02T03:04:00Z",
        last_index_duration_seconds=1.25,
    )
    compact = StatisticsWidget(compact=True)
    compact.set_statistics(statistics)
    assert set(compact._value_labels) == {"customer_count", "project_count"}
    assert "Kunden: 1" in compact.accessibleDescription()
    compact.set_loading()
    assert compact.status_label.isVisibleTo(compact)
    compact.set_error("offline")
    assert "offline" in compact.status_label.text()

    full = StatisticsWidget(compact=False)
    full.set_statistics(statistics)
    assert len(full._value_labels) == 10
    full.set_statistics(ApplicationStatistics())
    assert full._value_labels["total_file_size"].text() == "0 B"
    full.grid.addItem(
        QSpacerItem(1, 1, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed),
        99,
        0,
    )
    full._clear()


def _customers_for_search_rows():
    return [
        Customer(id=1, display_name="Leer"),
        Customer(id=2, display_name="Altpfad", folder_path="/tmp/alt"),
        Customer(
            id=3,
            display_name="Mehrpfad",
            folder_paths=("/tmp/a", "/tmp/b"),
            service_types=("Planung",),
            city="Berlin",
        ),
        Customer(
            id=4,
            display_name="Projekt",
            projects=(
                CustomerProject(source=SourcePath("primary", "kunden/projekt")),
            ),
        ),
    ]


def test_search_page_states_rows_signal_forwarding_and_statistics(application):
    page = SearchPage(document_search_enabled=True)
    assert page.statistics_widget.isVisibleTo(page)
    page.reset([])
    assert not page.customer_section._message.text()
    page.reset(_customers_for_search_rows(), keys=["one"], paths=["/mapped"])
    assert page.customer_section.row_count == 4
    rows = page.customer_section._rows
    assert rows[0].payload == "one" and rows[0].path == "/mapped"
    assert rows[1].payload == 2 and rows[1].path == "/tmp/alt"
    assert "Planung" in rows[2].accessibleDescription()
    assert "Berlin" in rows[2].accessibleName()
    assert rows[3].path == "kunden/projekt"

    customers = []
    paths = []
    page.customerActivated.connect(customers.append)
    page.openPathRequested.connect(paths.append)
    rows[0].activated.emit(rows[0].payload)
    rows[0].openPathRequested.emit(rows[0].path)
    assert customers == ["one"]
    assert paths == ["/mapped"]

    page.prepare_search()
    assert "läuft" in page.customer_section._message.text()
    page.show_short_query_hint()
    assert "zwei Zeichen" in page.folder_section._message.text()
    page.set_customer_error("customer")
    page.set_folder_error("folder")
    page.set_document_error("document")
    assert "document" in page.document_section._message.text()
    page.set_statistics(None)
    page.set_statistics(None, "statistics")
    page.set_statistics(ApplicationStatistics(customer_count=8))
    assert page.statistics_widget._value_labels["customer_count"].text() == "8"


def test_search_page_folders_documents_coverage_and_disabled_branches(application):
    page = SearchPage(document_search_enabled=True)
    page.set_folders(
        [
            {
                "folder_name": "Projekt A",
                "relative_path": "kunden/a",
                "file_count": 3,
                "folder_path": "/tmp/a",
                "navigation_key": ("primary", "kunden/a"),
            },
            {"folder_name": "", "file_count": 0, "folder_path": "/tmp/b"},
        ],
        5,
    )
    assert page.folder_section.row_count == 2
    assert page.folder_section._rows[0].payload == ("primary", "kunden/a")
    assert page.folder_section._rows[1].payload == "/tmp/b"

    page.set_documents(
        [
            {"filename": "report.pdf", "excerpt": "Treffer", "path": "/tmp/report.pdf"},
            {"path": "/tmp/fallback.txt"},
            {},
        ],
        3,
    )
    assert [row.title_label.text() for row in page.document_section._rows] == [
        "report.pdf",
        "fallback.txt",
        "Unbekannte Datei",
    ]
    docs = []
    page.openFileRequested.connect(docs.append)
    page.document_section._rows[0].openFileRequested.emit("/tmp/report.pdf")
    assert docs == ["/tmp/report.pdf"]

    page.set_document_coverage(None)
    page.set_document_coverage(SimpleNamespace(complete=True))
    page.set_document_coverage(
        SimpleNamespace(
            complete=False,
            completed_documents=0,
            total_documents=0,
            unavailable_shards=0,
        )
    )
    assert "0 %" in page.document_section._message.text()
    page.set_document_coverage(
        SimpleNamespace(
            complete=False,
            completed_documents=3,
            total_documents=4,
            unavailable_shards=2,
        )
    )
    assert "75 %" in page.document_section._message.text()
    assert "2 Shards" in page.document_section._message.text()

    disabled = SearchPage(document_search_enabled=False)
    disabled.prepare_search()
    disabled.show_short_query_hint()
    disabled.set_document_error("ignored")
    disabled.set_documents([{"filename": "ignored"}], 1)
    disabled.set_document_coverage(SimpleNamespace(complete=False))
    assert disabled.document_section.row_count == 0
    disabled.set_document_search_enabled(True)
    assert disabled.document_section._message.text() == "Suchbegriff eingeben"
    disabled.set_document_search_enabled(False)


def test_result_section_plain_and_signal_rows(application):
    section = _ResultSection("Test")
    plain = QWidget()
    row = ResultRow("A", "", 1, "/tmp/a")
    section.set_rows([plain, row], total=7)
    assert section.row_count == 2
    assert section.heading.text() == "Test (7)"
    section.set_note("Hinweis")
    assert section._message.text() == "Hinweis"
    section.set_rows([], total=0)
    assert section._message.text() == "Keine Treffer gefunden"
    section.set_message("")
    assert section.row_count == 0


@dataclass
class FakeFolderSettings:
    saved_sizes: object = None

    def value(self, _key, default=None):
        return default if self.saved_sizes is None else self.saved_sizes

    def setValue(self, key, value):
        self.key = key
        self.value_written = value


def _folder_details(tmp_path: Path):
    nested = tmp_path / "Unterordner" / "Tiefe"
    nested.mkdir(parents=True)
    root_file = tmp_path / "root.txt"
    nested_file = nested / "plan.pdf"
    explicit_file = tmp_path / "table.data"
    root_file.write_text("root", encoding="utf-8")
    nested_file.write_bytes(b"pdf")
    explicit_file.write_text("data", encoding="utf-8")
    return {
        "folder_path": str(tmp_path),
        "folder_name": "Projektordner",
        "file_count": 4,
        "total_size": 2 * 1024 * 1024,
        "service_types": ["Planung", "Beratung"],
        "subfolders": [
            {
                "name": "Unterordner",
                "path": str(tmp_path / "Unterordner"),
                "children": [
                    {"name": "Tiefe", "path": str(nested), "children": []},
                    {"name": "Leer", "path": "", "children": []},
                ],
            }
        ],
        "files": [
            {
                "filename": "root.txt",
                "path": str(root_file),
                "relative_dir": "",
                "file_size": 20,
                "modified_date": "2026-01-02T00:00:00",
            },
            {
                "filename": "plan.pdf",
                "path": str(nested_file),
                "relative_dir": "Unterordner/Tiefe",
                "file_size": 2048,
                "modified_date": "not-a-date",
            },
            {
                "filename": "table.data",
                "path": str(explicit_file),
                "relative_dir": "",
                "file_type": "csv",
                "file_size": 2 * 1024 * 1024,
                "modified_date": "",
            },
            {"filename": "without-type", "path": "", "file_size": 0},
        ],
    }


def test_folder_page_details_filters_tree_actions_and_layout(
    application, tmp_path, monkeypatch
):
    settings = FakeFolderSettings([300, 600])
    monkeypatch.setattr("papagui_client.gui.pages.folder_page.QSettings", lambda *_args: settings)
    page = FolderPage()
    page.file_viewer.open_file = Mock()
    page.file_viewer.shutdown = Mock()
    details = _folder_details(tmp_path)
    page.set_folder(details)
    assert page.folder_title.text() == "Projektordner"
    assert "Planung, Beratung" in page.folder_meta.text()
    assert page.file_list.topLevelItemCount() >= 3
    assert page.file_list.count() == page.file_list.topLevelItemCount()
    assert {page.file_type_filter.itemData(i) for i in range(page.file_type_filter.count())} == {
        "",
        "txt",
        "pdf",
        "csv",
    }

    root_folder = page.file_list.topLevelItem(0)
    deep_folder = root_folder.child(0)
    nested_file = deep_folder.child(0)
    page._open_selected_file(nested_file)
    page._open_selected_file(root_folder)
    empty = QTreeWidgetItem(["Empty"])
    page._open_selected_file(empty)
    page.file_viewer.open_file.assert_called_once_with(Path(nested_file.data(0, Qt.UserRole)))

    page.file_filter.setText("Tiefe")
    root_folder = page.file_list.topLevelItem(0)
    assert not root_folder.isHidden()
    page.file_filter.setText("no-match")
    page.file_filter.clear()
    page.file_type_filter.setCurrentIndex(page.file_type_filter.findData("csv"))
    visible_files = []

    def collect(item):
        if item.data(0, Qt.UserRole + 1) == "file" and not item.isHidden():
            visible_files.append(item.text(0))
        for index in range(item.childCount()):
            collect(item.child(index))

    for index in range(page.file_list.topLevelItemCount()):
        collect(page.file_list.topLevelItem(index))
    assert "table.data" in visible_files

    opened = []
    managed = []
    page.openPathRequested.connect(opened.append)
    page.manageCustomerRequested.connect(lambda path, title: managed.append((path, title)))
    page._open_folder()
    page._manage_customer()
    assert opened[-1] == str(tmp_path)
    assert managed == [(str(tmp_path), "Projektordner")]
    page.folder_path = ""
    page._open_folder()
    page._manage_customer()
    page._open_context_item("file", "/tmp/file")
    page._open_context_item("folder", "/tmp/folder")
    page._open_context_item("file", "")
    assert opened[-1] == "/tmp/folder"

    assert FolderPage._format_size(1) == "1 B"
    assert FolderPage._format_size(1024) == "1.0 KB"
    assert FolderPage._format_size(1024 * 1024) == "1.0 MB"
    assert FolderPage._path_key("") == ""
    assert FolderPage._path_key(str(tmp_path))

    page.splitter.setOrientation(Qt.Orientation.Horizontal)
    page._save_splitter_sizes()
    assert settings.key == "ui/folder_splitter_sizes"
    page.splitter.setOrientation(Qt.Orientation.Vertical)
    written = settings.value_written
    page._save_splitter_sizes()
    assert settings.value_written == written
    page.resize(800, 700)
    page.resizeEvent(QResizeEvent(QSize(800, 700), QSize(1_000, 700)))
    assert page.splitter.orientation() == Qt.Orientation.Vertical
    page.resize(1000, 700)
    page.resizeEvent(QResizeEvent(QSize(1_000, 700), QSize(800, 700)))
    assert page.splitter.orientation() == Qt.Orientation.Horizontal
    page.cleanup()
    page.file_viewer.shutdown.assert_called_once()
    page.close()


def test_folder_page_defaults_and_service_free_metadata(application, monkeypatch):
    settings = FakeFolderSettings("not-a-list")
    monkeypatch.setattr("papagui_client.gui.pages.folder_page.QSettings", lambda *_args: settings)
    page = FolderPage()
    page.set_folder({"folder_name": "", "files": [], "subfolders": []})
    assert page.folder_title.text() == "Ordner"
    assert page.folder_meta.text() == "0 Dateien · 0.00 MB"
    page.close()


def test_folder_page_loads_indexed_subfolders_when_expanded(
    application, tmp_path, monkeypatch
):
    settings = FakeFolderSettings()
    monkeypatch.setattr(
        "papagui_client.gui.pages.folder_page.QSettings", lambda *_args: settings
    )
    child_path = tmp_path / "Honorar"
    document_path = child_path / "Rechnung.xlsx"
    page = FolderPage()
    requested = []
    page.folderExpansionRequested.connect(requested.append)
    route = ("archive", "Blower Door/2020/Kita Rickling/Honorar")
    page.set_folder(
        {
            "folder_name": "Kita Rickling",
            "folder_path": str(tmp_path),
            "subfolders": [
                {
                    "name": "Honorar",
                    "path": str(child_path),
                    "navigation_key": route,
                    "children": [],
                    "children_loaded": False,
                }
            ],
            "files": [],
        }
    )

    folder = page.file_list.topLevelItem(0)
    assert folder.childCount() == 1
    assert folder.child(0).data(0, Qt.UserRole + 1) == "placeholder"
    folder.setExpanded(True)
    assert requested == [route]

    page.set_folder_children(
        route,
        {
            "subfolders": [],
            "files": [
                {
                    "filename": "Rechnung.xlsx",
                    "path": str(document_path),
                    "relative_dir": "Blower Door/2020/Kita Rickling/Honorar",
                    "file_type": "xlsx",
                }
            ],
        },
    )
    folder = page.file_list.topLevelItem(0)
    assert folder.isExpanded()
    assert folder.childCount() == 1
    assert folder.child(0).text(0) == "Rechnung.xlsx"
    page.close()


class FolderFakeMenu(FakeMenu):
    pass


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("Öffnen", "open"),
        ("Ordner im System öffnen", "folder"),
        ("Pfad kopieren", "copy"),
        ("", "none"),
    ],
)
def test_folder_context_menu_choices(application, monkeypatch, selection, expected):
    page = FolderPage()
    item = QTreeWidgetItem(["Datei"])
    item.setData(0, Qt.UserRole, "/tmp/file.txt")
    item.setData(0, Qt.UserRole + 1, "file")
    page.file_list.addTopLevelItem(item)
    FolderFakeMenu.selection = selection
    monkeypatch.setattr("papagui_client.gui.pages.folder_page.QMenu", FolderFakeMenu)
    monkeypatch.setattr(page.file_list, "itemAt", lambda _position: item)
    opened_files = []
    opened_paths = []
    page.openFileRequested.connect(opened_files.append)
    page.openPathRequested.connect(opened_paths.append)
    page._open_tree_context_menu(QPoint())
    assert opened_files == (["/tmp/file.txt"] if expected == "open" else [])
    assert opened_paths == (["/tmp/file.txt"] if expected == "folder" else [])
    if expected == "copy":
        assert QApplication.clipboard().text() == "/tmp/file.txt"
    monkeypatch.setattr(page.file_list, "itemAt", lambda _position: None)
    page._open_tree_context_menu(QPoint())
    page.close()


def test_folder_context_menu_empty_path_disables_actions(application, monkeypatch):
    page = FolderPage()
    item = QTreeWidgetItem(["Leer"])
    item.setData(0, Qt.UserRole, "")
    item.setData(0, Qt.UserRole + 1, "folder")
    page.file_list.addTopLevelItem(item)
    monkeypatch.setattr(page.file_list, "itemAt", lambda _position: item)
    for selection in ("Öffnen", "Ordner im System öffnen", "Pfad kopieren"):
        FolderFakeMenu.selection = selection
        monkeypatch.setattr("papagui_client.gui.pages.folder_page.QMenu", FolderFakeMenu)
        page._open_tree_context_menu(QPoint())
    page.close()


def test_mail_panel_and_centered_dialog_paths(application, monkeypatch):
    mail = MailPanel()
    assert mail.objectName() == "MailPanel"
    assert "Postfach" in mail.info_label.text()

    owner = QWidget()
    owner.resize(400, 300)
    owner.move(50, 50)
    dialog = CenteredPopupDialog(owner)
    dialog.resize(100, 80)
    owner.show()
    dialog.show()
    application.processEvents()
    dialog.center_on_parent()
    assert owner.frameGeometry().contains(dialog.frameGeometry().center())
    dialog.close()
    owner.close()

    class NoScreenApplication:
        @staticmethod
        def primaryScreen():
            return None

    monkeypatch.setattr(
        "papagui_client.gui.dialogs.centered_popup.QApplication",
        NoScreenApplication,
    )
    orphan = CenteredPopupDialog()
    orphan.resize(80, 60)
    orphan.center_on_parent()
    assert orphan.pos() == QPoint(-1, -1)
    orphan.close()
