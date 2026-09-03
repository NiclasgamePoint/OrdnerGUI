"""Regression contract for the restored v0.4.1 desktop presentation.

These checks deliberately use widget types, object names and visible copy
instead of pixel snapshots.  Platform font rendering can differ, while the
information architecture and the client/server boundary must not.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QLabel,
    QStackedWidget,
    QTabWidget,
    QWidget,
)

from papagui_contracts import Customer
from papagui_client.application.models import GlobalSearchQuery
from papagui_client.application.paths import SourceMapping
from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.gui.customer_detail import CustomerDetailWidget
from papagui_client.gui.customer_editor import CustomerEditorDialog
from papagui_client.gui.main import ClientMainWindow
from papagui_client.gui.pages import FolderPage, SearchPage
from papagui_client.gui.settings import ClientSettingsDialog
from papagui_client.gui.tray import ServerStatusBadge, ServerTrayWindow
from papagui_client.gui.widgets import AppHeader, IndexStatusBar, SearchFilterPopup
from papagui_client.presentation.coordinators import (
    NavigationCoordinator,
    SearchCoordinator,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


class _Sync:
    def start(self, *_args, **_kwargs):
        return None

    def stop(self):
        return None


class _Search:
    def history(self):
        return ()

    def facets(self, _source_id=None):
        return SimpleNamespace(domains=(), years=(), file_types=())

    def project_roots(self, _source_id=None):
        return ()

    def folders(self, _source_id=None, **_kwargs):
        return ()


class _Customers:
    def list(self):
        return ()

    def conflicts(self):
        return ()


class _ServerControl:
    def status(self):
        return {"state": "online", "index": {"state": "idle"}}


def _container(tmp_path):
    return SimpleNamespace(
        settings=SimpleNamespace(
            server_url="http://server.test",
            data_root=tmp_path,
        ),
        sync=_Sync(),
        search=_Search(),
        customers=_Customers(),
        navigation=NavigationCoordinator(),
        server_control=_ServerControl(),
    )


def _texts(parent: QWidget, object_name: str | None = None) -> set[str]:
    return {
        label.text()
        for label in parent.findChildren(QLabel)
        if object_name is None or label.objectName() == object_name
    }


def _has_shortcut(window: QWidget, sequence: str) -> bool:
    expected = QKeySequence(sequence)
    return any(
        shortcut.key().matches(expected)
        is QKeySequence.SequenceMatch.ExactMatch
        for shortcut in window.findChildren(QShortcut)
    )


def test_main_window_restores_v041_shell_without_primary_tabs(
    application, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "papagui_client.gui.main.QSystemTrayIcon.isSystemTrayAvailable",
        lambda: False,
    )
    window = ClientMainWindow(_container(tmp_path), automatic_sync=False)

    assert window.size().width() == 1400
    assert window.size().height() == 900
    assert window.minimumWidth() == 920
    assert window.minimumHeight() == 640

    root_layout = window.centralWidget().layout()
    assert root_layout.itemAt(0).widget() is window.header
    assert root_layout.itemAt(1).widget() is window.page_stack
    assert root_layout.itemAt(2).widget() is window.status_bar
    assert isinstance(window.header, AppHeader)
    assert isinstance(window.page_stack, QStackedWidget)
    assert window.page_stack.objectName() == "PageStack"
    assert isinstance(window.search_page, SearchPage)
    assert isinstance(window.customer_page, CustomerDetailWidget)
    assert isinstance(window.folder_page, FolderPage)
    assert isinstance(window.status_bar, IndexStatusBar)
    assert not isinstance(window.page_stack, QTabWidget)
    assert window.page_stack.currentWidget() is window.search_page

    assert isinstance(window.filter_popup, SearchFilterPopup)
    assert window.filter_popup.objectName() == "SearchFilterPopup"
    assert _has_shortcut(window, "Alt+Left")
    assert _has_shortcut(window, "Alt+Right")

    window.navigator.navigate("connection")
    assert window.page_stack.currentWidget() is window.connection_page
    window.navigator.back()
    assert window.page_stack.currentWidget() is window.search_page
    window.navigator.forward()
    assert window.page_stack.currentWidget() is window.connection_page

    window.close()


def test_tray_restores_v041_header_tabs_cards_and_status_badge(
    application, tmp_path
):
    window = ServerTrayWindow(_container(tmp_path))
    window._timer.stop()

    assert window.objectName() == "IndexControlWindow"
    assert window.minimumWidth() == 760
    assert window.minimumHeight() == 620
    assert isinstance(window.server_badge, ServerStatusBadge)
    assert window.server_badge.objectName() == "ServerStatusBadge"
    assert "Indexserver" in _texts(window, "IndexControlTitle")
    assert (
        "Docker-Dienst, Indexjobs und Generationen zentral verwalten"
        in _texts(window, "PopupCaption")
    )

    assert window.tabs.objectName() == "IndexControlTabs"
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "Übersicht",
        "Indexeinstellungen",
        "Aktivität",
    ]
    overview = window.tabs.widget(0)
    assert len(overview.findChildren(QFrame, "IndexControlCard")) == 4
    assert {
        "Server",
        "Aktueller Indexjob",
        "Dokumentinhalte",
        "Veröffentlichte Generation",
    }.issubset(_texts(overview, "IndexCardTitle"))

    window.shutdown()
    window.close()


def test_settings_restore_six_left_hand_v041_sections(application, tmp_path):
    settings = ClientSettings(
        server_url="https://server.test",
        data_root=tmp_path,
        api_token="secret",
        source_mappings=(SourceMapping("primary", linux="/mnt/data"),),
        sync_interval_seconds=3_600,
        theme=ClientTheme.LIGHT,
    )
    dialog = ClientSettingsDialog(settings)

    assert dialog.findChild(QFrame, "SettingsPopup") is not None
    assert dialog.nav_list.objectName() == "SettingsNav"
    assert [
        dialog.nav_list.item(index).text()
        for index in range(dialog.nav_list.count())
    ] == [
        "Allgemein",
        "Indexierung",
        "Suche",
        "Kundenerkennung",
        "Statistik",
        "Aussehen",
    ]
    assert dialog.stack.objectName() == "SettingsStack"
    assert dialog.stack.count() == 6
    assert dialog.findChild(QTabWidget) is None

    for index in range(dialog.nav_list.count()):
        dialog.nav_list.setCurrentRow(index)
        assert dialog.stack.currentIndex() == index
    dialog.close()


def test_customer_editor_restores_four_tabs_but_keeps_projects_read_only(
    application,
):
    dialog = CustomerEditorDialog(Customer(id=7, display_name="Muster GmbH"))

    assert dialog.findChild(QFrame, "CustomerEditorBody") is not None
    assert dialog.tabs.objectName() == "CustomerEditorTabs"
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "Stammdaten",
        "Kontakte",
        "Notiz",
        "Dateien",
    ]
    assert dialog.selected_folders_table.editTriggers() == (
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    assert not dialog.found_folders_table.isEnabled()
    assert "Server" in " ".join(_texts(dialog.tabs.widget(3)))
    dialog.close()


def test_customer_detail_restores_two_v041_cards(application):
    widget = CustomerDetailWidget()

    cards = widget.findChildren(QWidget, "PageCard")
    assert widget.objectName() == "CustomerPage"
    assert widget.splitter.objectName() == "PageSplitter"
    assert widget.splitter.count() == 2
    assert len(cards) == 2
    assert {"Kundendaten", "Dienstleistungen"}.issubset(
        _texts(widget, "PageTitle")
    )
    assert [
        widget.customer_tabs.tabText(index)
        for index in range(widget.customer_tabs.count())
    ] == ["Notizen", "Journal"]
    widget.close()


def test_search_coordinator_delegates_complete_read_only_catalog_surface():
    calls = []
    page = object()
    facets = object()
    folders = (object(),)
    details = object()
    projects = (object(),)

    class Catalog:
        def global_search(self, query):
            calls.append(("global_search", query))
            return page

        def facets(self, source_id=None):
            calls.append(("facets", source_id))
            return facets

        def folders(self, source_id=None, *, query="", project_only=False):
            calls.append(("folders", source_id, query, project_only))
            return folders

        def folder(self, source_id, relative_path):
            calls.append(("folder", source_id, relative_path))
            return details

        def project_roots(self, source_id=None):
            calls.append(("project_roots", source_id))
            return projects

        def history(self):
            calls.append(("history",))
            return ("Muster",)

    catalog = Catalog()
    sessions = SimpleNamespace(
        current=SimpleNamespace(catalog_search=catalog),
    )
    coordinator = SearchCoordinator(sessions)
    query = GlobalSearchQuery(text="Muster")

    assert coordinator.global_search(query) is page
    assert coordinator.facets("primary") is facets
    assert coordinator.folders(
        "primary", query="2026", project_only=True
    ) is folders
    assert coordinator.folder("primary", "2026/Muster") is details
    assert coordinator.project_roots("primary") is projects
    assert coordinator.history() == ("Muster",)
    assert calls == [
        ("global_search", query),
        ("facets", "primary"),
        ("folders", "primary", "2026", True),
        ("folder", "primary", "2026/Muster"),
        ("project_roots", "primary"),
        ("history",),
    ]


def test_restored_gui_does_not_import_server_or_index_writer_code():
    gui_root = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "client"
        / "src"
        / "papagui_client"
        / "gui"
    )
    forbidden_modules = ("papagui_server", "app.indexing", "app.infrastructure")
    forbidden_symbols = {"IndexBuilder", "IndexManager", "IndexWriter"}
    violations = []

    for path in sorted(gui_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_modules):
                        violations.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(forbidden_modules):
                    violations.append(f"{path.name}: from {module}")
                for alias in node.names:
                    if alias.name in forbidden_symbols:
                        violations.append(f"{path.name}: {alias.name}")

    assert violations == []
