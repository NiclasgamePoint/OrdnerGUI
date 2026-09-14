"""Branch-focused integration checks for the restored v0.4.1 client shell.

The fakes in this module implement the client-side ports only.  Besides keeping
the visual shell testable, that makes accidental re-coupling to server/indexer
implementation code immediately visible.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from threading import Event
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QProgressBar, QSystemTrayIcon

from papagui_contracts import (
    CatalogFacets,
    CatalogFolder,
    Customer,
    CustomerJournalEntry,
    CustomerProject,
    SourcePath,
)
from papagui_client.application.models import (
    CatalogFolderDetails,
    CatalogFolderHit,
    CatalogHit,
    CatalogRecord,
    CustomerSyncState,
    CustomerView,
    CustomerWriteResult,
    GlobalSearchHit,
    GlobalSearchKind,
    GlobalSearchPage,
    GlobalSearchRecord,
    JournalEntryView,
    JournalReplayResult,
    JournalWriteResult,
    ReplayResult,
    SyncResult,
)
from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.gui import launcher as launcher_module
from papagui_client.gui import main as main_module
from papagui_client.gui.launcher import TrayProcessLauncher
from papagui_client.gui.main import ClientMainWindow
from papagui_client.gui.tray import ServerStatusBadge, ServerTrayWindow
from papagui_client.gui.viewers.file import FileViewerWidget
from papagui_client.presentation.coordinators import (
    ClientPage,
    NavigationCoordinator,
    SearchCoordinator,
)
from papagui_client.presentation.tray import TrayHealth, TrayStatusViewModel


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


class _Sync:
    def __init__(self) -> None:
        self.stopped = 0

    def start(self, *_args, **_kwargs) -> None:
        return None

    def stop(self) -> None:
        self.stopped += 1

    def sync(self) -> SyncResult:
        return SyncResult()


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, source: SourcePath) -> Path:
        if source.source_id == "broken":
            raise OSError("mapping fehlt")
        return self.root / source.relative_path


class _Search:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.queries = []
        self.fail_global: Exception | None = None
        self.fail_facets = False
        self.fail_folder = False
        self.fail_statistics = False
        self.fail_history = False
        self.cleared = 0
        self.source = SourcePath("archive", "2026/Muster")
        self.folder_value = CatalogFolder(
            1,
            self.source,
            "Muster",
            file_count=2,
            total_size=4_096,
            last_modified="2026-08-01",
        )
        self.folder_hit = CatalogFolderHit(self.folder_value, root / "2026/Muster")
        self.child_hit = CatalogFolderHit(
            CatalogFolder(
                2,
                SourcePath("archive", "2026/Muster/Pläne"),
                "Pläne",
                parent_id=1,
            ),
            root / "2026/Muster/Pläne",
        )
        self.document_hit = CatalogHit(
            CatalogRecord(
                "doc:1",
                "archive",
                "2026/Muster/angebot.txt",
                "angebot.txt",
                "txt",
                project_name="Musterprojekt",
                modified_date="2026-08-01",
                file_size=42,
                relative_dir="2026/Muster",
            ),
            root / "2026/Muster/angebot.txt",
        )

    def history(self):
        if self.fail_history:
            raise OSError("history kaputt")
        return ("Muster", "Plan")

    def clear_history(self):
        self.cleared += 1

    def facets(self, _source_id=None):
        if self.fail_facets:
            raise OSError("facets kaputt")
        return CatalogFacets(("Beratung",), ("2026",), ("txt",))

    def project_roots(self, _source_id=None):
        if self.fail_statistics:
            raise OSError("statistik kaputt")
        return (SimpleNamespace(project=SimpleNamespace()),)

    def folders(self, _source_id=None, **_kwargs):
        if self.fail_statistics:
            raise OSError("statistik kaputt")
        return (self.folder_hit,)

    def folder(self, source_id, relative_path):
        self.folder_request = (source_id, relative_path)
        if self.fail_folder:
            raise OSError("ordner offline")
        return CatalogFolderDetails(
            self.folder_hit,
            (self.child_hit,),
            (self.document_hit,),
        )

    def global_search(self, query):
        self.queries.append(query)
        if self.fail_global is not None:
            raise self.fail_global
        items = (
            GlobalSearchHit(
                GlobalSearchRecord(
                    GlobalSearchKind.CUSTOMER,
                    "customer:7",
                    "Muster GmbH",
                    customer_id=7,
                )
            ),
            GlobalSearchHit(
                GlobalSearchRecord(
                    GlobalSearchKind.CUSTOMER,
                    "customer:404",
                    "Gelöschter Kunde",
                    customer_id=404,
                )
            ),
            GlobalSearchHit(
                GlobalSearchRecord(
                    GlobalSearchKind.FOLDER,
                    "folder:1",
                    "Muster Unterordner",
                    source_id="archive",
                    relative_path="2026/Muster/Pläne",
                    metadata={"folder": self.folder_value},
                ),
                self.root / "2026/Muster/Pläne",
            ),
            GlobalSearchHit(
                GlobalSearchRecord(
                    GlobalSearchKind.PROJECT,
                    "project:1",
                    "Muster Projekt",
                    source_id="archive",
                    relative_path="2026/Muster",
                    metadata={"project": SimpleNamespace(), "file_count": 5},
                ),
                self.root / "2026/Muster",
            ),
            GlobalSearchHit(
                GlobalSearchRecord(
                    GlobalSearchKind.DOCUMENT,
                    "document:1",
                    "angebot.txt",
                    "Muster im Dokument",
                    source_id="archive",
                    relative_path="2026/Muster/angebot.txt",
                ),
                self.root / "2026/Muster/angebot.txt",
            ),
        )
        return GlobalSearchPage(
            items,
            9,
            query.limit,
            query.offset,
            kind_counts={
                "customer": 2,
                "folder": 3,
                "project": 1,
                "document": 3,
            },
        )

    def search(self, _text, **_filters):
        return (self.document_hit,)


class _Customers:
    def __init__(self, root: Path) -> None:
        self.remote = Customer(
            id=7,
            revision=2,
            display_name="Muster GmbH",
            company="Muster GmbH",
            city="Berlin",
            service_types=("Beratung",),
            projects=(
                CustomerProject(
                    id=1,
                    source=SourcePath("archive", "2026/Muster"),
                    service_type="Beratung",
                ),
                CustomerProject(
                    id=2,
                    source=SourcePath("broken", "2025/Alt"),
                    service_type="Planung",
                ),
            ),
        )
        self.local = Customer(
            display_name="Muster Lokal",
            folder_path=str(root / "lokal"),
        )
        self.views = (
            CustomerView("7", self.remote),
            CustomerView("local:1", self.local, CustomerSyncState.PENDING),
        )
        self.saved = []
        self.deleted = []
        self.resolutions = []
        self.cases = ()

    def list(self):
        return self.views

    def save(self, customer, **kwargs):
        self.saved.append((customer, kwargs))
        return CustomerWriteResult(customer, False, ReplayResult())

    def delete(self, customer, **kwargs):
        self.deleted.append((customer, kwargs))
        return CustomerWriteResult(None, False, ReplayResult())

    def replay(self):
        return ReplayResult()

    def conflicts(self):
        return self.cases


class _Journals:
    def __init__(self) -> None:
        self.entry = CustomerJournalEntry(
            id=3,
            customer_id=7,
            entry_number=1,
            title="Telefonat",
            body="Notiz",
            revision=1,
        )
        self.views = (JournalEntryView("3", self.entry),)
        self.saved = []
        self.deleted = []
        self.discarded = []
        self.resolved = []

    def list(self, _customer_id):
        return self.views

    def save(self, *args, **kwargs):
        self.saved.append((args, kwargs))
        return JournalWriteResult(self.entry, False, JournalReplayResult())

    def delete(self, *args):
        self.deleted.append(args)
        return JournalWriteResult(None, False, JournalReplayResult())

    def discard(self, key):
        self.discarded.append(key)

    def conflicts(self):
        return ()

    def discard_conflict(self, case):
        self.resolved.append(("discard", case))

    def retry_against_current(self, case, entry=None):
        self.resolved.append(("retry", case, entry))


class _Review:
    def __init__(self) -> None:
        self.decisions = []

    def customer_suggestions(self, _customer_id, _state):
        return (), 2

    def decide_suggestion(self, *args):
        self.decisions.append(args)


class _ServerControl:
    def __init__(self) -> None:
        self.actions = []

    def status(self):
        return {"state": "online", "index": {"state": "idle"}}

    def index_action(self, action):
        self.actions.append(action)
        return {"accepted": True}

    def settings(self):
        return {"settings": {"interval_seconds": 3_600}}

    def save_settings(self, values):
        self.saved_settings = values
        return {"settings": values}

def _container(tmp_path: Path):
    settings = ClientSettings(
        server_url="http://server.test",
        data_root=tmp_path,
        theme=ClientTheme.LIGHT,
    )
    value = SimpleNamespace(
        settings=settings,
        config_sources={},
        onboarding_required=False,
        sync=_Sync(),
        search=_Search(tmp_path),
        customers=_Customers(tmp_path),
        journals=_Journals(),
        review_gateway=_Review(),
        paths=_Paths(tmp_path),
        navigation=NavigationCoordinator(),
        server_control=_ServerControl(),
    )

    def save_client_settings(updated):
        value.settings = updated
        return updated

    value.save_client_settings = save_client_settings
    return value


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    value = _container(tmp_path)
    widget = ClientMainWindow(value, automatic_sync=False)
    # Initial project summaries query the same fake search service as folder
    # navigation. Finish them before assertions about the latest navigation call.
    assert widget._pool.waitForDone(1_000)
    application.processEvents()
    yield widget, value
    widget.close()


def test_search_split_filters_overlay_and_error_paths(window):
    widget, value = window
    widget.filter_popup.include_subfolders_checkbox.setChecked(True)
    widget.header.search_input.setText("Muster")
    widget.domain_filter.setCurrentIndex(widget.domain_filter.findData("Beratung"))
    widget.year_filter.setCurrentIndex(widget.year_filter.findData("2026"))
    widget.file_type_filter.setCurrentIndex(widget.file_type_filter.findData("txt"))
    widget.start_full_search()

    assert not widget.search_debounce.isActive()
    assert widget.search_page.customer_section.row_count == 2
    assert widget.search_page.folder_section.row_count == 2
    assert widget.search_page.document_section.row_count == 0
    assert not widget.search_page.document_section.isVisible()
    query = value.search.queries[-1]
    assert (query.domain_folder, query.year, query.file_type) == (
        "Beratung",
        "2026",
        None,
    )
    assert query.kinds == (
        GlobalSearchKind.CUSTOMER,
        GlobalSearchKind.PROJECT,
        GlobalSearchKind.FOLDER,
    )
    assert widget._folder_routes[("archive", "2026/Muster")]
    assert "9 Treffer" in widget.status.text()

    widget.filter_popup.include_subfolders_checkbox.setChecked(False)
    widget.start_full_search()
    assert widget.search_page.folder_section.row_count == 1
    assert any(
        label.text().startswith("5 Dateien")
        for label in widget.search_page.folder_section.findChildren(QLabel)
    )

    for sort_index in range(widget.filter_popup.sort_combo.count()):
        widget.filter_popup.sort_combo.setCurrentIndex(sort_index)
        widget.start_full_search()
    assert {query.sort.value for query in value.search.queries} >= {
        "relevance",
        "modified",
        "name",
    }

    value.search.fail_global = OSError("Serverindex fehlt")
    widget.start_full_search()
    assert "Serverindex fehlt" in widget.status.text()
    value.search.fail_global = AttributeError("alte Suche")
    widget.start_full_search()
    assert "alte Suche" in widget.status.text()

    widget.header.search_input.setText("x")
    widget.start_full_search()
    assert "Mindestens" in widget.search_page.customer_section._message.text()
    widget.on_search_text_changed("x")
    widget.header.search_input.clear()
    widget.on_search_text_changed("")
    widget.header.search_input.setText("lang")
    widget.on_search_text_changed("lang")
    assert widget.search_debounce.isActive()


@pytest.mark.parametrize("count,expected", [(None, "Dateianzahl unbekannt"), (0, "0 Dateien")])
def test_folder_result_distinguishes_missing_count_from_empty_folder(window, count, expected):
    widget, _value = window
    widget.search_page.set_folders([{"folder_name": "Projekt", "file_count": count}], 1)
    assert expected in {
        label.text() for label in widget.search_page.folder_section.findChildren(QLabel)
    }


def test_search_metadata_filter_popup_and_statistics_fallback(window, monkeypatch):
    widget, value = window
    value.search.fail_facets = True
    widget._refresh_search_metadata()
    assert widget.domain_filter.count() == 1
    value.search.fail_facets = False
    value.search.fail_statistics = True
    widget._refresh_statistics()

    widget.header.search_input.setText("Muster")
    saved = []
    monkeypatch.setattr(widget.search_preferences, "save", lambda *args: saved.append(args))
    widget._on_filter_changed()
    assert saved

    widget.open_filter_popup()
    assert widget.filter_popup.isVisible()
    widget.open_filter_popup()
    assert not widget.filter_popup.isVisible()


def test_folder_routes_navigation_summaries_and_native_open(window, monkeypatch):
    widget, value = window
    warnings = []
    information = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: information.append(args))

    assert widget._coerce_folder_route(("archive", "2026/Muster")) == (
        "archive",
        "2026/Muster",
    )
    widget._folder_routes["shown"] = ("archive", "2026/Muster")
    assert widget._coerce_folder_route("shown") == ("archive", "2026/Muster")
    assert widget._coerce_folder_route(str(value.paths.root / "2026/Muster")) == (
        "archive",
        "2026/Muster",
    )
    assert widget._coerce_folder_route("2026/Muster") == ("archive", "2026/Muster")
    assert widget._coerce_folder_route("archive:2026/Muster") == (
        "archive",
        "2026/Muster",
    )
    assert widget._coerce_folder_route("missing") is None

    widget.open_folder_page(("archive", "2026/Muster"))
    assert widget.page_stack.currentWidget() is widget.folder_page
    assert value.search.folder_request == ("archive", "2026/Muster")
    presentation = widget._folder_details(value.search.folder("archive", "2026/Muster"))
    assert presentation["folder_name"] == "Muster"
    assert presentation["subfolders"][0]["name"] == "Pläne"
    assert presentation["files"][0]["filename"] == "angebot.txt"

    summaries = widget._customer_project_summaries(value.customers.remote)
    assert summaries["archive:2026/Muster"]["file_count"] == 2
    assert summaries["broken:2025/Alt"]["local_path"] == ""

    widget.manage_folder_customer(str(value.paths.root / "2026/Muster"), "Muster")
    assert widget.navigator.current.page == "customer"
    widget.manage_folder_customer("/not/mapped", "Unbekannt")
    assert information

    widget.open_folder_page("not-there")
    assert "Pfadmapping" in widget.status.text()
    value.search.fail_folder = True
    widget.navigator.navigate("folder", ("archive", "2026/Muster"))
    assert any("Ordner konnte" in args[1] for args in warnings)

    monkeypatch.setattr(main_module.QDesktopServices, "openUrl", lambda _url: False)
    widget.open_native_file("")
    widget.open_native_path("/not/reachable")
    assert len(warnings) >= 3
    monkeypatch.setattr(main_module.QDesktopServices, "openUrl", lambda _url: True)
    widget.open_native_file("/tmp/file")


def test_main_navigation_system_tray_and_launcher_failure(window, monkeypatch):
    widget, value = window
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    widget.open_customer_page(7)
    assert widget.page_stack.currentWidget() is widget.customer_page
    widget.open_customer_page("unknown")
    assert "existiert nicht" in widget.status.text()
    widget.open_customer_page_without_navigation("unknown")

    widget._navigate(ClientPage.CONNECTION)
    assert widget.page_stack.currentWidget() is widget.connection_page
    widget._navigate(ClientPage.SEARCH)
    widget._navigate(ClientPage.CUSTOMERS)
    widget._tab_changed(99)

    class _BrokenLauncher:
        def show(self):
            raise OSError("Prozessstart fehlgeschlagen")

    monkeypatch.setattr(main_module, "TrayProcessLauncher", _BrokenLauncher)
    widget._show_index_tray()
    assert warnings[-1][1] == "Indexserver"

    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    widget._init_system_tray()
    assert widget.tray_icon is not None
    widget._update_system_tray()
    assert "PapaGUI" in widget.tray_icon.toolTip()
    widget.show()
    widget._on_tray_activated(QSystemTrayIcon.ActivationReason.Context)
    assert widget.isVisible()
    widget._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert not widget.isVisible()
    widget._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert widget.isVisible()


def test_customer_page_does_not_wait_for_server_details(window, application):
    widget, value = window
    widget._pool.waitForDone(1_000)
    application.processEvents()
    widget.customer_list.setCurrentRow(-1)

    entered = Event()
    release = Event()
    calls = []

    class _SlowReview:
        def customer_suggestions(self, customer_id, _state):
            calls.append(customer_id)
            entered.set()
            release.wait(2)
            return (), 2

    value.review_gateway = _SlowReview()
    started = perf_counter()
    widget.open_customer_page("7")
    elapsed = perf_counter() - started

    try:
        assert elapsed < 0.5
        assert widget.page_stack.currentWidget() is widget.customer_page
        assert widget.customer_detail.heading.text() == "Muster GmbH"
        assert not widget.customer_detail.review_button.isEnabled()
        assert entered.wait(1)
    finally:
        release.set()

    assert widget._pool.waitForDone(2_000)
    application.processEvents()
    assert calls == [7]
    assert widget.customer_detail.review_button.isEnabled()


def test_project_details_journal_and_suggestion_paths(window, application, monkeypatch):
    widget, value = window
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    widget.customer_list.setCurrentRow(0)
    widget._show_customer_detail(0)
    assert widget.customer_detail.heading.text() == "Muster GmbH"
    widget._show_customer_detail(-1)

    entry = value.journals.entry
    widget.customer_list.setCurrentRow(0)
    widget._create_journal_entry(entry)
    view = value.journals.views[0]

    class _AcceptedJournal:
        class DialogCode:
            Accepted = 1

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return 1

        def entry(self):
            return entry

    monkeypatch.setattr(main_module, "JournalEditorDialog", _AcceptedJournal)
    widget.add_journal_entry()
    widget.edit_journal_entry(view)
    widget.delete_journal_entry(JournalEntryView("local:j", CustomerJournalEntry(title="Lokal")))
    widget.delete_journal_entry(view)
    assert value.journals.saved
    assert value.journals.deleted
    assert value.journals.discarded == ["local:j"]

    suggestion = SimpleNamespace(customer_id=7, id=12)
    widget.decide_customer_suggestion(suggestion, "accept", 2)
    assert widget._pool.waitForDone(1_000)
    application.processEvents()
    assert value.review_gateway.decisions == [(7, 12, "accept", 2)]

    value.journals.save = Mock(side_effect=OSError("journal offline"))
    widget._create_journal_entry(entry)
    value.review_gateway.decide_suggestion = Mock(side_effect=OSError("review offline"))
    widget.decide_customer_suggestion(suggestion, "reject", 2)
    assert widget._pool.waitForDone(1_000)
    application.processEvents()
    assert len(warnings) == 2


def test_remaining_main_shell_branches(window, application, monkeypatch):
    widget, value = window
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    # Compatibility adapter and history failure after a successful global search.
    widget.header.search_input.setText("Muster")
    value.search.fail_history = True
    widget.catalog_browser.run_search()
    value.search.fail_history = False
    value.search.fail_global = AttributeError("legacy")
    value.search.search = Mock(side_effect=OSError("alte Suche defekt"))
    widget.start_full_search()
    assert "legacy" in widget.search_page.customer_section._message.text()
    assert "legacy" in widget.search_page.folder_section._message.text()
    value.search.fail_global = None

    projects = (
        CustomerProject(service_type="ohne Quelle"),
        CustomerProject(source=SourcePath("broken", "2024/Defekt")),
        CustomerProject(source=SourcePath("archive", "2026/Muster")),
    )
    customer = Customer(
        display_name="Pfadtest",
        folder_path="source://archive/ignored",
        folder_paths=("source://archive/ignored", "/legacy/customer"),
        projects=projects,
    )
    assert Path(widget._customer_local_path(customer)).parts[-2:] == ("2026", "Muster")
    assert widget._customer_local_path(
        Customer(display_name="Alt", folder_path="/legacy/customer")
    ) == "/legacy/customer"
    assert widget._customer_local_path(Customer(display_name="Leer")) == ""
    value.customers.views = value.customers.views + (CustomerView("paths", customer),)
    widget.refresh_customers()
    assert widget._coerce_folder_route("unmapped") is None

    from papagui_client.gui.navigation import NavigationEntry

    widget._show_route(NavigationEntry("unknown"))
    launched = []
    monkeypatch.setattr(
        main_module,
        "TrayProcessLauncher",
        lambda: SimpleNamespace(show=lambda: launched.append(True)),
    )
    widget.open_index_diagnostics()
    assert launched == [True]

    # Summary ignores projects without a portable source and tolerates lookup errors.
    value.search.fail_folder = True
    summaries = widget._customer_project_summaries(customer)
    assert "broken:2024/Defekt" in summaries
    value.search.fail_folder = False

    # No-selection and rejected-dialog guards are part of the visible lifecycle.
    widget.customer_list.setCurrentRow(-1)
    widget.add_journal_entry()
    widget._create_journal_entry(value.journals.entry)
    widget.edit_journal_entry(value.journals.views[0])
    widget.delete_journal_entry(value.journals.views[0])

    class _RejectedJournal:
        class DialogCode:
            Accepted = 1

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return 0

    monkeypatch.setattr(main_module, "JournalEditorDialog", _RejectedJournal)
    widget.customer_list.setCurrentRow(0)
    widget.add_journal_entry()
    widget.edit_journal_entry(value.journals.views[0])

    value.journals.save = Mock(side_effect=OSError("journal save"))
    widget.edit_journal_entry = ClientMainWindow.edit_journal_entry.__get__(widget)

    class _AcceptedJournal(_RejectedJournal):
        def exec(self):
            return 1

        def entry(self):
            return value.journals.entry

    monkeypatch.setattr(main_module, "JournalEditorDialog", _AcceptedJournal)
    widget.edit_journal_entry(value.journals.views[0])
    value.journals.delete = Mock(side_effect=OSError("journal delete"))
    widget.delete_journal_entry(value.journals.views[0])

    # Activation keeps non-customer hits inert and resolves customer hits by key.
    widget._search_result_activated("7")
    widget._search_result_activated(
        GlobalSearchHit(
            GlobalSearchRecord(GlobalSearchKind.DOCUMENT, "doc", "Dokument")
        )
    )
    widget._search_result_activated(
        GlobalSearchHit(
            GlobalSearchRecord(GlobalSearchKind.CUSTOMER, "empty", "Ohne ID")
        )
    )
    widget._search_result_activated(
        GlobalSearchHit(
            GlobalSearchRecord(
                GlobalSearchKind.CUSTOMER,
                "customer:7",
                "Muster GmbH",
                customer_id=7,
            )
        )
    )
    assert widget.customer_list.currentRow() == 0

    widget.customer_list.setCurrentRow(1)
    widget._save_customer_from_detail(value.customers.local)
    widget.customer_list.setCurrentRow(0)
    widget._save_customer_from_detail(value.customers.remote)
    assert value.customers.saved[-2][1]["local_key"] == "local:1"
    assert value.customers.saved[-1][1]["local_key"] is None


def test_main_editor_delete_signal_onboarding_and_sync_journal(
    application, tmp_path, monkeypatch
):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    value = _container(tmp_path)
    value.onboarding_required = True
    onboarding = []
    monkeypatch.setattr(
        ClientMainWindow,
        "open_settings",
        lambda self, *args, **kwargs: onboarding.append(kwargs),
    )
    callbacks = []
    monkeypatch.setattr(main_module.QTimer, "singleShot", lambda _delay, callback: callbacks.append(callback))
    widget = ClientMainWindow(value, automatic_sync=False)
    callbacks[-1]()
    assert onboarding == [{"onboarding": True}]

    class _Signal:
        def connect(self, callback):
            callback(value.customers.local)

    class _DeletingEditor:
        class DialogCode:
            Accepted = 1

        deleteRequested = _Signal()

        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return 1

        def customer(self):
            return value.customers.local

    monkeypatch.setattr(main_module, "CustomerEditorDialog", _DeletingEditor)
    widget.customer_list.setCurrentRow(1)
    widget.edit_customer()
    assert value.customers.deleted[-1][1]["local_key"] == "local:1"

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    value.customers.delete = Mock(side_effect=OSError("delete offline"))
    widget._delete_customer_value(value.customers.local, "local:1")
    widget._sync_complete(
        SyncResult(
            journal_replay=JournalReplayResult(
                conflicts=(SimpleNamespace(idempotency_key="journal"),)
            )
        )
    )
    assert warnings
    widget.close()


def test_launcher_visible_commands_and_platform_edges(monkeypatch):
    launcher = TrayProcessLauncher()
    calls = []
    monkeypatch.setattr(launcher, "_start", calls.append)
    launcher.show()
    assert calls[-1][-1] != "--background"
    launcher.start()
    assert calls[-1][-1] == "--background"

    assert launcher._macos_bundle(Path("/tmp/PapaGUI.app/Contents/MacOS/client")) == Path(
        "/tmp/PapaGUI.app"
    )
    assert launcher._macos_bundle(Path("/tmp/client")) is None

    popen = Mock()
    monkeypatch.setattr(launcher_module.subprocess, "Popen", popen)
    monkeypatch.setattr(launcher_module, "os", SimpleNamespace(**vars(launcher_module.os)))
    monkeypatch.setattr(launcher_module, "sys", SimpleNamespace(**vars(launcher_module.sys)))
    monkeypatch.setattr(launcher_module, "Path", PurePosixPath)
    monkeypatch.setattr(launcher_module.os, "name", "posix")
    launcher_module.TrayProcessLauncher._start(["tray", "--visible"])
    assert popen.call_args.kwargs["start_new_session"] is True

    monkeypatch.setattr(launcher_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher_module.sys, "platform", "darwin")
    monkeypatch.setattr(launcher_module.sys, "executable", "/tmp/client")
    assert launcher.command(background=False) == ["/tmp/papagui-tray"]


def test_theme_application_and_main_entrypoint_lifecycle(application, monkeypatch):
    themed = []

    class _Theme:
        def set_mode(self, value):
            themed.append(("mode", value))

        def apply(self, application):
            themed.append(("apply", application))

    class _PaletteApplication:
        def setPalette(self, _palette):
            return None

    monkeypatch.setattr(main_module, "ThemeManager", _Theme)
    palette_application = _PaletteApplication()
    main_module.apply_application_theme(palette_application, ClientTheme.DARK)
    assert themed == [("mode", "dark"), ("apply", palette_application)]

    class _StylesheetApplication:
        def setStyleSheet(self, stylesheet):
            self.stylesheet = stylesheet

    monkeypatch.setattr(main_module, "theme_stylesheet", lambda *_args: "fallback")
    stylesheet_application = _StylesheetApplication()
    main_module.apply_application_theme(stylesheet_application, ClientTheme.SYSTEM)
    assert stylesheet_application.stylesheet == "fallback"

    class _Application:
        _papagui_window = None

        def setApplicationName(self, name):
            self.name = name

        def setWindowIcon(self, icon):
            assert not icon.isNull()

        def exec(self):
            return 23

    application = _Application()

    class _ApplicationFactory:
        @staticmethod
        def instance():
            return application

    launched = []

    class _FailingLauncher:
        def start(self):
            launched.append("start")
            raise OSError("platform rejects detached process")

    created_window = object()
    shown = []
    monkeypatch.setattr(main_module, "QApplication", _ApplicationFactory)
    monkeypatch.setattr(main_module, "TrayProcessLauncher", _FailingLauncher)
    monkeypatch.setattr(main_module, "apply_application_theme", lambda *args: themed.append(args))
    monkeypatch.setattr(main_module, "ClientMainWindow", lambda *_args, **_kwargs: created_window)
    monkeypatch.setattr(main_module, "show_main_window", lambda *args: shown.append(args))
    container = SimpleNamespace(settings=SimpleNamespace(theme=ClientTheme.LIGHT))
    assert main_module.run_main_gui(container, automatic_sync=False) == 23
    assert launched == ["start"]
    assert shown == [(application, created_window)]
    assert application._papagui_window is created_window


def test_file_viewer_native_open_and_conversion_failure_edges(
    application, tmp_path, monkeypatch
):
    viewer = FileViewerWidget()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(
        "papagui_client.gui.viewers.file.QDesktopServices.openUrl",
        lambda _url: False,
    )
    viewer.open_externally()
    viewer.open_containing_folder()
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"legacy")
    viewer.current_file = source
    viewer.open_externally()
    viewer.open_containing_folder()
    assert len(warnings) == 2

    class _Worker:
        def take_converter(self):
            return None

    viewer._on_conversion_completed(
        viewer._load_generation,
        "word_to_pdf",
        SimpleNamespace(path=None, text=None, converted=True, tool="test"),
        "",
        _Worker(),
    )
    assert "keine PDF" in viewer.text_viewer.editor.toPlainText()
    viewer._on_conversion_completed(
        viewer._load_generation - 1,
        "word_to_pdf",
        None,
        "",
        _Worker(),
    )
    viewer.shutdown()
    viewer.close()


def test_tray_detailed_status_helpers_actions_and_settings(application, tmp_path, monkeypatch):
    value = _container(tmp_path)
    window = ServerTrayWindow(value)
    window._timer.stop()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args) or QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    payload = {
        "state": "degraded",
        "server_version": "0.4.2",
        "source_id": "archive",
        "source_available": False,
        "uptime_seconds": 90_061,
        "message": "NAS langsam",
        "queued_action": "full_rebuild",
        "resumable": True,
        "active_index_generation": "idx-4",
        "active_customer_generation": "cust-8",
        "backups": {"index": 3, "customers": 2},
        "retention": {"state": "warning", "failures": "eine Datei"},
        "settings": {"interval_seconds": 7_200},
        "index": {
            "state": "running",
            "run_id": "run-1",
            "message": "arbeitet",
            "progress": {
                "phase": "document content",
                "processed_items": 5,
                "total_items": 9,
                "failed_items": 1,
                "current_source": {
                    "source_id": "archive",
                    "relative_path": "2026/Muster/angebot.pdf",
                },
            },
        },
    }
    window._show_status(payload)
    assert "Problem" in window.server_status_label.text()
    assert "90" not in window.server_detail_label.text()
    assert "NAS langsam" in window.server_detail_label.text()
    assert window.progress_bar.maximum() == 9
    assert "Dokumentinhalte" in window.content_status_label.text()
    assert "idx-4" in window.generation_label.text()
    assert "Index 3" in window.backup_label.text()
    window._show_status(payload)
    assert window.activity.count() == 1

    window._apply_status_details(
        {
            "state": "online",
            "source_available": True,
            "uptime_seconds": "bad",
            "backups": 1,
            "index": {"state": "completed", "progress": {"processed_items": 2}},
        }
    )
    assert "abgeschlossen" in window.status_label.text()
    assert "1 von 3" in window.backup_label.text()
    window._apply_status_details({"index": {"state": "failed"}})
    window._apply_status_details({"index": {"state": "idle", "phase": "scan"}})

    window._apply_view_model(
        TrayStatusViewModel(TrayHealth.OFFLINE, "", "", "offline", "details")
    )
    window._apply_view_model(
        TrayStatusViewModel(TrayHealth.PROBLEM, "", "", "problem", "details")
    )
    window._apply_view_model(
        TrayStatusViewModel(TrayHealth.ONLINE, "", "", "online", "details")
    )

    tasks = []
    window._start_task = tasks.append
    window.run_action("start")
    assert tasks
    window._confirm_rebuild()
    window._confirm_restart()
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    window._confirm_delete()
    assert len(tasks) == 4
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args) or QMessageBox.StandardButton.No,
    )

    window._apply_settings({"settings": {"interval_seconds": 3_600}})
    window._mark_settings_dirty()
    assert window._settings_dirty
    window.save_settings()
    assert len(tasks) == 5
    window._settings_busy = True
    window.save_settings()
    window.load_settings()
    window._settings_busy = False
    window._settings_dirty = False
    window.load_settings(force=True)
    assert len(tasks) == 6
    window._settings_failed("kaputt")
    assert warnings

    assert window._integer(True, 4) == 4
    assert window._integer("7", 0) == 7
    assert window._integer("x", 3) == 3
    assert window._format_duration(59) == "00:00:59"
    assert window._format_duration(90_061).startswith("1 d")
    assert window._format_interval(3_600) == "1 Stunde"
    assert window._format_interval(7_200) == "2 Stunden"
    assert window._format_interval(900) == "15 Minuten"
    assert window._current_source({"current_path": "/legacy"}) == "/legacy"
    assert window._current_source({"current_source": {"source_id": "a"}}) == "a"
    bar = QProgressBar()
    window._set_progress(bar, 0, 0, True)
    assert bar.maximum() == 0
    window._set_progress(bar, 0, 0, False)
    assert bar.maximum() == 100
    window._refresh_finished()
    assert not window._refreshing
    window.shutdown()
    window.close()


def test_tray_missing_control_badge_and_search_clear_history(application, tmp_path, monkeypatch):
    value = _container(tmp_path)
    value.server_control = None
    window = ServerTrayWindow(value)
    window._timer.stop()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    window.refresh_status()
    window.run_action("start")
    assert "Keine Serversteuerung" in window.server_detail_label.text()
    assert warnings

    badge = ServerStatusBadge()
    initial = badge.text()
    badge.advance()
    assert badge.text() == initial
    badge.set_state("indexing", "INDEXLAUF")
    badge.advance()
    assert badge.text() != initial
    window.shutdown()
    window.close()

    search = value.search
    coordinator = SearchCoordinator(SimpleNamespace(current=SimpleNamespace(catalog_search=search)))
    coordinator.clear_history()
    assert search.cleared == 1
