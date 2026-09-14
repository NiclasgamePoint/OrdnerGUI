"""v0.4.1 desktop shell backed exclusively by 0.4.2 client services."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
import sys

from PySide6.QtCore import QThreadPool, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from papagui_client.composition import ClientContainer
from papagui_client.adapters.http_api import ApiConflictError
from papagui_client.adapters.http_review import SuggestionPage
from papagui_client.presentation.coordinators import ClientPage, ConflictCase
from papagui_client.application.models import (
    GlobalSearchHit,
    GlobalSearchKind,
    GlobalSearchQuery,
    GlobalSearchSort,
)

from .customer_detail import CustomerDetailWidget, JournalEditorDialog
from .customer_editor import CustomerEditorDialog
from .dialogs import OnboardingDialog
from .icons import application_icon, set_process_identity
from .legacy_models import ApplicationStatistics, SearchPreferences, SearchSort
from .launcher import TrayProcessLauncher
from .navigation import NavigationController
from .pages import FolderPage, SearchPage
from .settings import ClientSettingsDialog, theme_stylesheet
from .tasks import BackgroundTask
from .theme import AppearanceSettings, ThemeManager, build_stylesheet, save_appearance
from .timers import QtTimerAdapter
from .widgets import AppHeader, IndexStatusBar, SearchFilterPopup


STYLE = build_stylesheet("light", "#2db89d", 100, 13)


@dataclass(frozen=True, slots=True)
class _CustomerDetailLoadResult:
    """I/O result that can safely cross from the worker into the GUI thread."""

    request_id: int
    customer_key: str
    summaries: dict[str, dict]
    journal_views: tuple
    suggestions: tuple
    suggestion_revision: int
    suggestion_error: str = ""
    suggestion_total: int | None = None
    suggestion_has_more: bool = False
    recognition: dict = field(default_factory=dict)
    offline: bool = False


class _CatalogBrowserCompatibility:
    """Keep the short-lived 0.4.2 widget API available to integrations/tests.

    The object intentionally delegates to the restored header/page instead of
    constructing a second, hidden GUI.
    """

    def __init__(self, window: "ClientMainWindow") -> None:
        self._window = window
        self.query_input = window.header.search_input

    def run_search(self, _checked: bool = False) -> None:
        self._window.start_full_search()

    def shutdown(self) -> None:
        return None


class ClientMainWindow(QMainWindow):
    def __init__(self, container: ClientContainer, *, automatic_sync: bool = True):
        super().__init__()
        self._container = container
        self._pool = QThreadPool(self)
        self._tasks: set[BackgroundTask] = set()
        self._customer_views = []
        self._syncing = False
        self._settings_saving = False
        self._saved_configuration = None
        self._settings_onboarding = False
        self._closing = False
        self._customer_detail_request_id = 0
        self._suggestion_write_busy = False
        self._recognition_poll = QTimer(self)
        self._recognition_poll.setSingleShot(True)
        self._recognition_poll.setInterval(2500)
        self._recognition_poll.timeout.connect(self._poll_customer_recognition)
        self._folder_routes: dict[object, tuple[str, str]] = {}
        self._last_search_page = None
        self._application_statistics = ApplicationStatistics()
        self.tray_icon: QSystemTrayIcon | None = None
        self.setWindowTitle("PapaGUI - Kundenmanagement System")
        self.setWindowIcon(application_icon("client"))
        self.setGeometry(100, 100, 1400, 900)
        self.setMinimumSize(920, 640)
        self.navigator = NavigationController("search", self)
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(250)
        self.search_debounce.timeout.connect(self.start_full_search)
        self._build()
        self.refresh_customers()
        self._refresh_search_metadata()
        self._init_system_tray()
        self._sync_timer = QtTimerAdapter(self)
        self._initial_index_timer = QTimer(self)
        self._initial_index_timer.setSingleShot(True)
        self._initial_index_timer.timeout.connect(self.synchronize)
        if automatic_sync:
            self._container.sync.start(self._sync_timer, self.synchronize, immediate=True)
        if getattr(self._container, "onboarding_required", False):
            QTimer.singleShot(0, lambda: self.open_settings(onboarding=True))

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("RootWidget")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        history = []
        try:
            history = list(self._container.search.history())
        except Exception:
            pass
        self.header = AppHeader(history, document_search_enabled=False)
        layout.addWidget(self.header)

        self.filter_popup = SearchFilterPopup(self, file_type_filter_enabled=False)
        self.domain_filter = self.filter_popup.domain_combo
        self.year_filter = self.filter_popup.year_combo
        self.file_type_filter = self.filter_popup.file_type_combo
        self.search_preferences = SearchPreferences()
        sort_order, include_subfolders = self.search_preferences.load()
        self.filter_popup.sort_combo.setCurrentIndex(
            max(0, self.filter_popup.sort_combo.findData(sort_order))
        )
        self.filter_popup.include_subfolders_checkbox.setChecked(include_subfolders)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("PageStack")
        self.search_page = SearchPage(document_search_enabled=False)
        self.customer_detail = CustomerDetailWidget()
        self.customer_page = self.customer_detail
        self.connection_page = self._connection_tab()
        self.folder_page = FolderPage()
        for page in (
            self.search_page,
            self.customer_page,
            self.connection_page,
            self.folder_page,
        ):
            self.page_stack.addWidget(page)
        layout.addWidget(self.page_stack, 1)

        self.status_bar = IndexStatusBar()
        self.status = self.status_bar.status_label
        self.status_bar.set_text("Lokale Daten bereit")
        layout.addWidget(self.status_bar)

        # Compatibility state for the interim 0.4.2 tab shell.  These controls
        # are models for selection/status only and are not part of the visible
        # restored v0.4.1 hierarchy.
        self.tabs = self.page_stack
        self.customer_list = QListWidget(self)
        self.customer_list.hide()
        self.customer_list.itemDoubleClicked.connect(lambda _item: self.edit_customer())
        self.customer_list.currentRowChanged.connect(self._show_customer_detail)
        self.queue_status = QLabel("Offline-Warteschlange: 0", self)
        self.queue_status.hide()
        self.catalog_browser = _CatalogBrowserCompatibility(self)

        self.header.queryChanged.connect(self.on_search_text_changed)
        self.header.searchRequested.connect(self.start_full_search)
        self.header.filterRequested.connect(self.open_filter_popup)
        self.header.settingsRequested.connect(self.open_settings)
        self.filter_popup.filtersChanged.connect(self._on_filter_changed)
        self.search_page.customerActivated.connect(self.open_customer_page)
        self.search_page.folderActivated.connect(self.open_folder_page)
        self.search_page.openFileRequested.connect(self.open_native_file)
        self.search_page.openPathRequested.connect(self.open_native_path)
        if hasattr(self.customer_detail, "backRequested"):
            self.customer_detail.backRequested.connect(self.navigator.back)
        if hasattr(self.customer_detail, "customerEditRequested"):
            self.customer_detail.customerEditRequested.connect(self.edit_customer)
        if hasattr(self.customer_detail, "customerSaveRequested"):
            self.customer_detail.customerSaveRequested.connect(
                self._save_customer_from_detail
            )
        if hasattr(self.customer_detail, "journalCreateRequested"):
            self.customer_detail.journalCreateRequested.connect(
                self._create_journal_entry
            )
        if hasattr(self.customer_detail, "folderActivated"):
            self.customer_detail.folderActivated.connect(self.open_folder_page)
        if hasattr(self.customer_detail, "openPathRequested"):
            self.customer_detail.openPathRequested.connect(self.open_native_path)
        self.customer_detail.journalAddRequested.connect(self.add_journal_entry)
        self.customer_detail.journalEditRequested.connect(self.edit_journal_entry)
        self.customer_detail.journalDeleteRequested.connect(self.delete_journal_entry)
        self.customer_detail.suggestionDecisionRequested.connect(
            self.decide_customer_suggestion
        )
        self.customer_detail.suggestionPageRequested.connect(self.load_suggestion_page)
        self.customer_detail.suggestionSourceRequested.connect(self.open_suggestion_source)
        self.customer_detail.recognitionRequested.connect(self.start_customer_recognition)
        self.folder_page.backRequested.connect(self.navigator.back)
        self.folder_page.folderExpansionRequested.connect(
            self._load_folder_children
        )
        self.folder_page.openFileRequested.connect(self.open_native_file)
        self.folder_page.openPathRequested.connect(self.open_native_path)
        self.folder_page.manageCustomerRequested.connect(self.manage_folder_customer)
        self.status_bar.detailsRequested.connect(self.open_index_diagnostics)
        self.status_bar.textChanged.connect(self._update_system_tray)
        self.navigator.routeChanged.connect(self._show_route)

        back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        back_shortcut.activated.connect(self.navigator.back)
        forward_shortcut = QShortcut(QKeySequence("Alt+Right"), self)
        forward_shortcut.activated.connect(self.navigator.forward)

        self._container.navigation.subscribe(self._navigate)
        self.setCentralWidget(root)

    def _connection_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageCard")
        layout = QVBoxLayout(page)
        title = QLabel("Client-Verbindung")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        self.server_label = QLabel()
        self.data_root_label = QLabel()
        layout.addWidget(self.server_label)
        layout.addWidget(self.data_root_label)
        self._refresh_connection_labels()
        layout.addStretch()
        return page

    def _refresh_connection_labels(self) -> None:
        self.server_label.setText(f"Server: {self._container.settings.server_url or '–'}")
        self.data_root_label.setText(
            f"Lokaler Datenspeicher: {self._container.settings.data_root}"
        )

    def _refresh_search_metadata(self) -> None:
        for combo in (self.domain_filter, self.year_filter, self.file_type_filter):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Alle", "")
            combo.blockSignals(False)
        try:
            facets = self._container.search.facets()
        except Exception:
            facets = None
        values = (
            getattr(facets, "domains", ()),
            getattr(facets, "years", ()),
            getattr(facets, "file_types", ()),
        )
        for combo, entries in zip(
            (self.domain_filter, self.year_filter, self.file_type_filter),
            values,
            strict=True,
        ):
            combo.blockSignals(True)
            for value in entries:
                combo.addItem(str(value), str(value))
            combo.blockSignals(False)
        self._refresh_statistics()

    def _refresh_statistics(self) -> None:
        customers = [view.customer for view in self._customer_views]
        try:
            projects = tuple(self._container.search.project_roots())
            folders = tuple(self._container.search.folders())
        except Exception:
            projects = ()
            folders = ()
        # Folder totals include descendants; count only roots for the overview.
        root_folders = [hit for hit in folders if hit.folder.parent_id is None]
        statistics = ApplicationStatistics(
            customer_count=len(customers),
            contact_count=sum(len(customer.contacts) for customer in customers),
            project_count=len(projects),
            service_count=len(
                {
                    service
                    for customer in customers
                    for service in customer.service_types
                    if service
                }
            ),
            file_count=sum(hit.folder.file_count for hit in root_folders),
            total_file_size=sum(hit.folder.total_size for hit in root_folders),
        )
        self._application_statistics = statistics
        self.search_page.set_statistics(statistics)

    def on_search_text_changed(self, _text: str) -> None:
        self.navigator.navigate("search")
        self.search_debounce.stop()
        query = self.header.query()
        if not query:
            self._reset_search_page()
            self._refresh_statistics()
            return
        if len(query) < 2:
            self.search_page.show_short_query_hint()
            return
        self.search_debounce.start()

    def start_full_search(self) -> None:
        # Pressing Enter or changing a filter may arrive before the pending
        # debounce timeout.  Consume it here so the same result set is not
        # queried and rebuilt a second time 250 ms later.
        self.search_debounce.stop()
        query_text = self.header.query()
        self.navigator.navigate("search")
        if len(query_text) < 2:
            self.search_page.show_short_query_hint()
            return
        self.search_page.prepare_search()
        sort_map = {
            SearchSort.RELEVANCE: GlobalSearchSort.RELEVANCE,
            SearchSort.DATE: GlobalSearchSort.MODIFIED,
            SearchSort.ALPHABETICAL: GlobalSearchSort.NAME,
        }
        try:
            kinds = [GlobalSearchKind.CUSTOMER, GlobalSearchKind.PROJECT]
            if self.filter_popup.include_subfolders_checkbox.isChecked():
                kinds.append(GlobalSearchKind.FOLDER)
            query = GlobalSearchQuery(
                text=query_text,
                kinds=tuple(kinds),
                domain_folder=str(self.domain_filter.currentData() or "") or None,
                year=str(self.year_filter.currentData() or "") or None,
                file_type=None,
                sort=sort_map.get(
                    self.filter_popup.sort_combo.currentData(),
                    GlobalSearchSort.RELEVANCE,
                ),
                # ResultRow is a rich QWidget.  Keep its bounded presentation
                # cost predictable instead of constructing hundreds of hidden
                # file-result widgets in Qt's main thread.
                limit=250,
            )
            page = self._container.search.global_search(query)
        except Exception as exc:
            self.search_page.set_customer_error(str(exc))
            self.search_page.set_folder_error(str(exc))
            self.search_page.set_document_error(str(exc))
            self.status_bar.set_text(f"Suche nicht verfügbar · {exc}")
            return
        self._last_search_page = page
        by_id = {
            view.customer.id: (view.key, view.customer)
            for view in self._customer_views
            if view.customer.id is not None
        }
        customer_rows: list[tuple[object, object]] = []
        folder_rows: list[dict] = []
        self._folder_routes.clear()
        for hit in page.items:
            record = hit.record
            local_path = str(hit.local_path or "")
            if record.kind is GlobalSearchKind.CUSTOMER:
                found = by_id.get(record.customer_id)
                if found is not None:
                    customer_rows.append(found)
                continue
            if record.kind in {GlobalSearchKind.FOLDER, GlobalSearchKind.PROJECT}:
                if (
                    record.kind is GlobalSearchKind.FOLDER
                    and not self.filter_popup.include_subfolders_checkbox.isChecked()
                ):
                    continue
                route = (record.source_id, record.relative_path)
                self._folder_routes[route] = route
                metadata = record.metadata or {}
                folder = metadata.get("folder")
                file_count = metadata.get("file_count", getattr(folder, "file_count", None))
                folder_rows.append(
                    {
                        "folder_name": record.title,
                        "relative_path": record.relative_path,
                        "folder_path": local_path,
                        "file_count": file_count,
                        "navigation_key": route,
                    }
                )
                continue
        # Local overlay customers are deliberately searchable before their next
        # server snapshot and therefore may not yet be present in the catalog.
        known_keys = {key for key, _customer in customer_rows}
        needle = query_text.casefold()
        for view in self._customer_views:
            customer = view.customer
            haystack = " ".join(
                (customer.display_name, customer.company, customer.city)
            ).casefold()
            if needle in haystack and view.key not in known_keys:
                customer_rows.append((view.key, customer))
        self.search_page.set_customers(
            [customer for _key, customer in customer_rows],
            int(page.kind_counts.get(GlobalSearchKind.CUSTOMER.value, len(customer_rows))),
            keys=[key for key, _customer in customer_rows],
            paths=[self._customer_local_path(customer) for _key, customer in customer_rows],
        )
        included_folder_kinds = (
            (GlobalSearchKind.FOLDER, GlobalSearchKind.PROJECT)
            if self.filter_popup.include_subfolders_checkbox.isChecked()
            else (GlobalSearchKind.PROJECT,)
        )
        folder_total = sum(
            int(page.kind_counts.get(kind.value, 0)) for kind in included_folder_kinds
        )
        self.search_page.set_folders(folder_rows, folder_total or len(folder_rows))
        try:
            self.header.set_history(list(self._container.search.history()))
        except Exception:
            pass
        self.status_bar.set_text(f"Suche abgeschlossen · {page.total} Treffer")

    def _customer_local_path(self, customer) -> str:
        for project in customer.projects:
            if project.source is None:
                continue
            try:
                return str(self._container.paths.resolve(project.source))
            except Exception:
                continue
        for value in (*customer.folder_paths, customer.folder_path):
            if value and not str(value).startswith("source://"):
                return str(value)
        return ""

    def open_filter_popup(self) -> None:
        if self.filter_popup.isVisible():
            self.filter_popup.close()
            return
        origin = self.header.filter_button.mapToGlobal(
            self.header.filter_button.rect().bottomLeft()
        )
        screen = self.header.filter_button.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 8, min(origin.x(), area.right() - self.filter_popup.width() - 8))
            height = max(self.filter_popup.sizeHint().height(), 300)
            y = origin.y() + 6
            if y + height > area.bottom():
                y = self.header.filter_button.mapToGlobal(
                    self.header.filter_button.rect().topLeft()
                ).y() - height - 6
            origin.setX(x)
            origin.setY(max(area.top() + 8, y))
        self.filter_popup.move(origin)
        self.filter_popup.show()

    def _on_filter_changed(self) -> None:
        self.search_preferences.save(
            self.filter_popup.sort_combo.currentData(),
            self.filter_popup.include_subfolders_checkbox.isChecked(),
        )
        self.header.set_filter_count(self.filter_popup.active_filter_count())
        if len(self.header.query()) >= 2:
            self.start_full_search()

    def open_customer_page(self, key: object) -> None:
        normalized = str(key)
        for view in self._customer_views:
            if view.key == normalized or (
                view.customer.id is not None and str(view.customer.id) == normalized
            ):
                self.navigator.navigate("customer", view.key)
                return
        self.status_bar.set_text("Der ausgewählte Kunde existiert nicht mehr.")

    def open_folder_page(self, payload: object) -> None:
        route = self._coerce_folder_route(payload)
        if route is None:
            self.status_bar.set_text("Für diesen Ordner fehlt ein lokales Pfadmapping.")
            return
        self.navigator.navigate("folder", route)

    def _coerce_folder_route(self, payload: object) -> tuple[str, str] | None:
        if isinstance(payload, (list, tuple)) and len(payload) == 2:
            return str(payload[0]), str(payload[1])
        route = self._folder_routes.get(payload)
        if route is not None:
            return route
        candidate = str(payload or "")
        for view in self._customer_views:
            for project in view.customer.projects:
                source = project.source
                if source is None:
                    continue
                try:
                    local_path = str(self._container.paths.resolve(source))
                except Exception:
                    local_path = ""
                source_key = f"{source.source_id}:{source.relative_path}"
                if candidate in {local_path, source.relative_path, source_key}:
                    return source.source_id, source.relative_path
        return None

    def _show_route(self, entry) -> None:
        if self._closing:
            return
        if entry.page == "search":
            self.page_stack.setCurrentWidget(self.search_page)
            return
        if entry.page == "customer":
            self.open_customer_page_without_navigation(entry.payload)
            return
        if entry.page == "connection":
            self.page_stack.setCurrentWidget(self.connection_page)
            return
        if entry.page != "folder":
            return
        source_id, relative_path = entry.payload
        try:
            details = self._container.search.folder(source_id, relative_path)
        except Exception as exc:
            QMessageBox.warning(self, "Ordner konnte nicht geladen werden", str(exc))
            QTimer.singleShot(0, self.navigator.back)
            return
        presentation = self._folder_details(details)
        self.folder_page.set_folder(presentation)
        self.page_stack.setCurrentWidget(self.folder_page)
        self.status_bar.set_text(
            f"Ordner: {presentation['folder_name']} · {presentation['file_count']} Dateien"
        )

    def open_customer_page_without_navigation(self, key: object) -> None:
        normalized = str(key)
        for row, view in enumerate(self._customer_views):
            if view.key == normalized:
                self.page_stack.setCurrentWidget(self.customer_page)
                if self.customer_list.currentRow() != row:
                    self.customer_list.setCurrentRow(row)
                else:
                    self._show_customer_detail(row)
                return
        self.status_bar.set_text("Der ausgewählte Kunde existiert nicht mehr.")
        QTimer.singleShot(0, self.navigator.back)

    @staticmethod
    def _folder_details(details) -> dict:
        folder = details.folder.folder
        local_root = str(details.folder.local_path)
        children = [
            {
                "name": child.folder.name,
                "path": str(child.local_path),
                "children": [],
                "children_loaded": False,
                "navigation_key": (
                    child.folder.source.source_id,
                    child.folder.source.relative_path,
                ),
            }
            for child in details.children
        ]
        files = [
            {
                "filename": hit.record.filename,
                "file_type": hit.record.file_type,
                "file_size": hit.record.file_size,
                "modified_date": hit.record.modified_date,
                "relative_dir": hit.record.relative_dir,
                "path": str(hit.local_path),
            }
            for hit in details.documents
        ]
        return {
            "folder_name": folder.name,
            "folder_path": local_root,
            "file_count": folder.file_count or len(files),
            "total_size": folder.total_size,
            "service_types": [],
            "subfolders": children,
            "files": files,
        }

    def _load_folder_children(self, payload: object) -> None:
        route = self._coerce_folder_route(payload)
        if route is None:
            self.folder_page.folder_loading_failed(payload)
            self.status_bar.set_text("Für diesen Unterordner fehlt ein Pfadmapping.")
            return
        try:
            details = self._container.search.folder(*route)
        except Exception as exc:
            self.folder_page.folder_loading_failed(route)
            self.status_bar.set_text(f"Unterordner konnte nicht geladen werden · {exc}")
            return
        self.folder_page.set_folder_children(route, self._folder_details(details))

    def manage_folder_customer(self, folder_path: str, _folder_name: str) -> None:
        for row, view in enumerate(self._customer_views):
            for project in view.customer.projects:
                source = project.source
                if source is None:
                    continue
                try:
                    local_path = str(self._container.paths.resolve(source))
                except Exception:
                    continue
                if local_path == folder_path:
                    self.customer_list.setCurrentRow(row)
                    self.navigator.navigate("customer", view.key)
                    return
        QMessageBox.information(
            self,
            "Kundenzuordnung wird serverseitig verwaltet",
            "Dieser Ordner ist noch keinem Kunden zugeordnet. Die Zuordnung wird "
            "durch die Kundenerkennung des Indexservers erstellt und anschließend synchronisiert.",
        )

    def open_native_file(self, path: str) -> None:
        self._open_native(path, "Datei")

    def open_native_path(self, path: str) -> None:
        self._open_native(path, "Ordner")

    def _open_native(self, path: str, label: str) -> None:
        if not path or not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(
                self,
                f"{label} konnte nicht geöffnet werden",
                f"Der lokale Pfad ist nicht erreichbar:\n{path or '–'}",
            )

    def open_index_diagnostics(self) -> None:
        self._show_index_tray()

    def _init_system_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        menu = QMenu(self)
        open_action = QAction("PapaGUI öffnen", menu)
        open_action.triggered.connect(self._show_from_tray)
        menu.addAction(open_action)
        server_action = QAction("Indexserver", menu)
        server_action.triggered.connect(self._show_index_tray)
        menu.addAction(server_action)
        menu.addSeparator()
        close_action = QAction("Beenden", menu)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)
        self.tray_icon = QSystemTrayIcon(
            self.windowIcon(), self
        )
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self._update_system_tray()
        self.tray_icon.show()

    def _show_index_tray(self) -> None:
        try:
            TrayProcessLauncher().show()
        except OSError as exc:
            QMessageBox.warning(self, "Indexserver", str(exc))

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason != QSystemTrayIcon.ActivationReason.Trigger:
            return
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self._show_from_tray()

    def _update_system_tray(self, *_args) -> None:
        if self.tray_icon is not None:
            self.tray_icon.setToolTip(f"PapaGUI\n{self.status_bar.status_label.text()}")

    def open_settings(self, _checked: bool = False, *, onboarding: bool = False) -> None:
        if self._settings_saving or self._closing:
            return
        if onboarding:
            self.open_onboarding()
            return
        dialog = ClientSettingsDialog(
            self._container.settings,
            sources=getattr(self._container, "config_sources", {}),
            statistics=self._application_statistics,
            parent=self,
        )
        index_server_requested = getattr(dialog, "indexServerRequested", None)
        if index_server_requested is not None:
            index_server_requested.connect(self._show_index_tray)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._save_client_settings(dialog.settings(), appearance=dialog.appearance_settings())

    def open_onboarding(self) -> None:
        """Collect only the client-side values required for first use."""
        if self._settings_saving or self._closing:
            return
        dialog = OnboardingDialog(
            self._container.settings,
            sources=getattr(self._container, "config_sources", {}),
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            self.status_bar.set_text(
                "Noch keine Datenquelle eingerichtet · über Einstellungen fortfahren"
            )
            return
        self._save_client_settings(dialog.settings(), onboarding=True)

    def _save_client_settings(
        self, settings, *, onboarding: bool = False, appearance: AppearanceSettings | None = None
    ) -> None:
        if self._settings_saving or self._closing:
            return
        self._settings_saving = True
        self._settings_onboarding = onboarding
        self.header.settings_button.setEnabled(False)
        self.status_bar.set_text("Einstellungen werden gespeichert …")

        def persist():
            resolved = self._container.persist_client_settings(settings)
            if appearance is not None:
                save_appearance(appearance)
            return resolved

        task = BackgroundTask(persist)
        task.signals.succeeded.connect(self._client_settings_persisted)
        task.signals.failed.connect(self._client_settings_save_failed)
        self._start_task(task)

    def _client_settings_persisted(self, resolved) -> None:
        self._saved_configuration = resolved
        self._apply_saved_client_settings()

    def _apply_saved_client_settings(self) -> None:
        if self._saved_configuration is None or self._closing:
            return
        if self._syncing:
            self.status_bar.set_text(
                "Einstellungen gespeichert · Übernahme nach dem laufenden Abgleich …"
            )
            return
        resolved = self._saved_configuration
        self._saved_configuration = None
        try:
            effective = self._container.apply_client_settings(resolved)
            application = QApplication.instance()
            if application is not None:
                apply_application_theme(application, effective.theme)
            self._refresh_connection_labels()
        except Exception as exc:
            self._client_settings_save_failed(str(exc))
            return
        # Preferences do not change the active catalog or its statistics. Avoid
        # running those database queries (and clearing search filters) on save.
        self._settings_saving = False
        self.header.settings_button.setEnabled(True)
        self.status_bar.set_text(
            "Einrichtung abgeschlossen"
            if self._settings_onboarding
            else "Client-Einstellungen gespeichert"
        )

    def _client_settings_save_failed(self, error: str) -> None:
        self._saved_configuration = None
        self._settings_saving = False
        self.header.settings_button.setEnabled(True)
        self.status_bar.set_text("Einstellungen konnten nicht übernommen werden")
        QMessageBox.warning(self, "Einstellungen nicht gespeichert", error)

    def synchronize(self) -> None:
        if self._syncing or self._closing:
            return
        if self._settings_saving:
            # Preserve periodic and first-generation retries while settings I/O
            # is pending, without starting a request against the old connection.
            self._initial_index_timer.start(1_000)
            return
        self._syncing = True
        self.status_bar.set_text("Synchronisierung läuft …")
        task = BackgroundTask(self._container.sync.sync)
        task.signals.succeeded.connect(self._sync_complete)
        task.signals.failed.connect(self._sync_failed)
        task.signals.finished.connect(self._sync_finished)
        self._start_task(task)

    def _sync_complete(self, result) -> None:
        if result.awaiting_generation and self._sync_timer.active:
            self._initial_index_timer.start(5_000)
        else:
            self._initial_index_timer.stop()
        if result.awaiting_generation:
            message = "Server erreichbar · Noch kein fertiger Index verfügbar."
        elif result.changed:
            message = "Synchronisiert · " + ", ".join(result.changed_components)
        else:
            message = "Synchronisiert · aktuell"
        self.status_bar.set_text(message)
        if result.customer_replay is not None:
            self._handle_conflicts(result.customer_replay.conflicts)
        if result.journal_replay is not None:
            self._handle_journal_replay(result.journal_replay)
        self.refresh_customers()
        self._refresh_search_metadata()
        if self.catalog_browser.query_input.text().strip():
            self.catalog_browser.run_search()

    def _sync_finished(self) -> None:
        self._syncing = False
        self.status_bar.set_busy(False)
        self._apply_saved_client_settings()

    def _sync_failed(self, error: str) -> None:
        self._initial_index_timer.stop()
        self.status_bar.set_text(f"Offline · {error}")
        self.status_bar.set_busy(False)

    def refresh_customers(self) -> None:
        current_item = self.customer_list.currentItem()
        selected_key = (
            str(current_item.data(Qt.ItemDataRole.UserRole))
            if current_item is not None
            else ""
        )
        try:
            self._customer_views = list(self._container.customers.list())
        except Exception as exc:
            self.customer_list.clear()
            self.customer_list.addItem(f"Kundendaten nicht verfügbar: {exc}")
            return
        self.customer_list.clear()
        self.customer_detail.set_customer(None)
        pending = 0
        badges = {
            "pending": "⏳ lokal vorgemerkt",
            "conflict": "⚠ Konflikt",
            "awaiting_snapshot": "✓ übertragen",
            "synced": "",
        }
        for view in self._customer_views:
            customer = view.customer
            location = f" · {customer.city}" if customer.city else ""
            badge = badges[view.sync_state.value]
            if view.sync_state.value in {"pending", "conflict"}:
                pending += 1
            suffix = f"  [{badge}]" if badge else ""
            item = QListWidgetItem(f"{customer.display_name}{location}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, view.key)
            self.customer_list.addItem(item)
        self.queue_status.setText(f"Offline-Warteschlange: {pending}")
        if pending:
            self.status_bar.set_text(
                f"Lokale Daten bereit · {pending} Änderung(en) warten auf Synchronisation"
            )
        if self._customer_views:
            selected_row = next(
                (
                    row
                    for row, view in enumerate(self._customer_views)
                    if view.key == selected_key
                ),
                0,
            )
            self.customer_list.setCurrentRow(selected_row)
        if not self.header.query():
            self._reset_search_page()
            self._refresh_statistics()

    def _reset_search_page(self) -> None:
        self.search_page.reset(
            [view.customer for view in self._customer_views],
            keys=[view.key for view in self._customer_views],
            paths=[self._customer_local_path(view.customer) for view in self._customer_views],
        )

    def new_customer(self) -> None:
        dialog = CustomerEditorDialog(parent=self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self._save_customer(dialog.customer())

    def edit_customer(self) -> None:
        selected = self._selected_customer()
        if selected is None:
            return
        dialog = CustomerEditorDialog(selected.customer, parent=self)
        deleted = [False]
        delete_signal = getattr(dialog, "deleteRequested", None)
        if delete_signal is not None:
            def delete_from_editor(customer) -> None:
                deleted[0] = True
                self._delete_customer_value(customer, selected.key)

            delete_signal.connect(delete_from_editor)
        if dialog.exec() == dialog.DialogCode.Accepted and not deleted[0]:
            self._save_customer(
                dialog.customer(),
                local_key=selected.key if selected.customer.id is None else None,
            )

    def _delete_customer_value(self, customer, local_key: str | None = None) -> None:
        try:
            result = self._container.customers.delete(
                customer,
                local_key=local_key if customer.id is None else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Löschen fehlgeschlagen", str(exc))
            return
        self._handle_write_result(result)

    def delete_customer(self) -> None:
        selected = self._selected_customer()
        if selected is None:
            return
        answer = QMessageBox.question(
            self,
            "Kunde löschen",
            f"Soll „{selected.customer.display_name}“ wirklich gelöscht werden?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self._container.customers.delete(
                selected.customer,
                local_key=selected.key if selected.customer.id is None else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Löschen fehlgeschlagen", str(exc))
            return
        self._handle_write_result(result)

    def replay_customers(self) -> None:
        try:
            result = self._container.customers.replay()
        except Exception as exc:
            QMessageBox.warning(self, "Übertragung fehlgeschlagen", str(exc))
            return
        self._handle_conflicts(result.conflicts)
        self.refresh_customers()

    def _show_customer_detail(self, row: int) -> None:
        self._recognition_poll.stop()
        self.customer_detail.set_suggestions_busy(False)
        self._customer_detail_request_id += 1
        request_id = self._customer_detail_request_id
        selected = self._customer_views[row] if 0 <= row < len(self._customer_views) else None
        customer = selected.customer if selected is not None else None
        self.customer_detail.set_customer(customer)
        self.customer_detail.set_journal(())
        if customer is None or customer.id is None:
            self.customer_detail.set_suggestions((), 0)
            return

        self.customer_detail.set_suggestions_loading(customer.revision)
        customer_key = selected.key
        task = BackgroundTask(
            lambda: self._load_customer_detail(
                request_id,
                customer_key,
                customer,
            )
        )
        task.signals.succeeded.connect(self._apply_customer_detail)
        self._start_task(task)

    def _load_customer_detail(
        self,
        request_id: int,
        customer_key: str,
        customer,
    ) -> _CustomerDetailLoadResult:
        """Load all potentially blocking detail data outside Qt's GUI thread."""

        summaries = self._customer_project_summaries(customer)
        try:
            journals = getattr(self._container, "journals", None)
            journal_views = tuple(journals.list(customer.id)) if journals is not None else ()
        except Exception:
            journal_views = ()
        suggestion_error = ""
        page = None
        try:
            page = self._read_suggestion_page(customer.id)
            suggestions, revision = page.suggestions, page.revision
        except Exception as exc:
            suggestions, revision = (), customer.revision
            suggestion_error = str(exc)
        return _CustomerDetailLoadResult(
            request_id=request_id,
            customer_key=customer_key,
            summaries=summaries,
            journal_views=journal_views,
            suggestions=tuple(suggestions),
            suggestion_revision=revision,
            suggestion_error=suggestion_error,
            suggestion_total=page.total if page else None,
            suggestion_has_more=page.has_more if page else False,
            recognition=page.recognition if page else {},
            offline=page.offline if page else False,
        )

    def _apply_customer_detail(self, result: _CustomerDetailLoadResult) -> None:
        """Apply only the newest response; older requests may finish later."""

        if self._closing or result.request_id != self._customer_detail_request_id:
            return
        selected = self._selected_customer()
        if selected is None or selected.key != result.customer_key:
            return
        self.customer_detail.set_project_summaries(result.summaries)
        self.customer_detail.set_journal(result.journal_views)
        if result.suggestion_error:
            self.customer_detail.set_suggestions_error(
                result.suggestion_error,
                result.suggestion_revision,
            )
        else:
            self.customer_detail.set_suggestions(
                result.suggestions,
                result.suggestion_revision,
                total=result.suggestion_total, has_more=result.suggestion_has_more,
                recognition=result.recognition, offline=result.offline,
            )
            self._schedule_recognition_poll(result.recognition)

    def _read_suggestion_page(self, customer_id, offset=0) -> SuggestionPage:
        gateway = self._container.review_gateway
        page_reader = getattr(gateway, "customer_suggestions_page", None)
        if callable(page_reader):
            page = page_reader(customer_id, "pending", limit=30, offset=offset)
            if not page.offline:
                try:
                    status = gateway.recognition_status(customer_id)
                    page = replace(page, recognition=status)
                except Exception:
                    # A legacy server may serve candidates without a status route.
                    pass
            return page
        suggestions, revision = gateway.customer_suggestions(customer_id, "pending")
        return SuggestionPage(tuple(suggestions[offset:offset + 30]), revision,
                              len(suggestions), offset + 30 < len(suggestions))

    def load_suggestion_page(self, offset: int = 0) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None or self._suggestion_write_busy:
            self.customer_detail.set_suggestions_busy(False)
            return
        self._customer_detail_request_id += 1
        request_id = self._customer_detail_request_id
        customer_id = selected.customer.id
        self.customer_detail.set_suggestions_busy(True)

        def read_page():
            try:
                return customer_id, request_id, offset, self._read_suggestion_page(customer_id, offset), None
            except Exception as error:
                return customer_id, request_id, offset, None, error

        task = BackgroundTask(read_page)
        task.signals.succeeded.connect(self._apply_suggestion_page)
        self._start_task(task)

    def _apply_suggestion_page(self, result) -> None:
        customer_id, request_id, offset, page, error = result
        if not self._review_result_is_current(customer_id, request_id):
            return
        self.customer_detail.set_suggestions_busy(False)
        if error is not None:
            self.customer_detail.set_suggestions_error(str(error), self.customer_detail._suggestion_revision)
            return
        if offset and not page.suggestions and page.total:
            # A decision can resolve several alternatives and remove the last page.
            self.load_suggestion_page(min(offset - 30, ((page.total - 1) // 30) * 30))
            return
        self.customer_detail.set_suggestions(
            page.suggestions, page.revision, total=page.total, offset=offset if page.total else 0,
            has_more=page.has_more, recognition=page.recognition, offline=page.offline,
        )
        self._schedule_recognition_poll(page.recognition)

    def _review_result_is_current(self, customer_id: int, request_id: int) -> bool:
        selected = self._selected_customer()
        return bool(not self._closing and request_id == self._customer_detail_request_id
                    and selected is not None and selected.customer.id == customer_id)

    def open_suggestion_source(self, source) -> None:
        try:
            path = str(self._container.paths.resolve(source))
        except Exception as error:
            QMessageBox.warning(self, "Quelle konnte nicht geöffnet werden", str(error))
            return
        self.open_native_file(path)

    def start_customer_recognition(self, mode: str) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None or self._suggestion_write_busy:
            return
        customer_id = selected.customer.id
        request_id = self._customer_detail_request_id
        self.customer_detail.set_suggestions_busy(True)

        def start():
            try:
                response = self._container.review_gateway.start_customer_recognition(customer_id, mode)
                return customer_id, request_id, response, None
            except Exception as error:
                return customer_id, request_id, None, error

        task = BackgroundTask(start)
        task.signals.succeeded.connect(self._apply_recognition_start)
        self._start_task(task)

    def _apply_recognition_start(self, result) -> None:
        customer_id, request_id, _response, error = result
        if not self._review_result_is_current(customer_id, request_id):
            return
        self.customer_detail.set_suggestions_busy(False)
        if error is not None:
            self.customer_detail.set_suggestions_error(str(error), self.customer_detail._suggestion_revision)
            return
        self.customer_detail._recognition_status = {"state": "running"}
        self.customer_detail.recognition_status_label.setText("Kundendaten werden auf dem Server geprüft …")
        self.customer_detail._update_suggestion_dialog()
        self._recognition_poll.start()

    def _schedule_recognition_poll(self, recognition) -> None:
        if recognition.get("state") == "running":
            self._recognition_poll.start()

    def _poll_customer_recognition(self) -> None:
        if self._suggestion_write_busy:
            self._recognition_poll.start()
        else:
            self.load_suggestion_page(self.customer_detail._suggestion_offset)

    def _customer_project_summaries(self, customer) -> dict[str, dict]:
        summaries: dict[str, dict] = {}
        for project in customer.projects:
            source = project.source
            if source is None:
                continue
            key = f"{source.source_id}:{source.relative_path}"
            summary: dict[str, object] = {}
            try:
                summary["local_path"] = str(self._container.paths.resolve(source))
            except Exception:
                summary["local_path"] = ""
            try:
                details = self._container.search.folder(
                    source.source_id, source.relative_path
                )
                summary["file_count"] = details.folder.folder.file_count
                summary["last_modified"] = details.folder.folder.last_modified or ""
            except Exception:
                pass
            summaries[key] = summary
        return summaries

    def add_journal_entry(self) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None:
            return
        dialog = JournalEditorDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._create_journal_entry(dialog.entry())

    def _create_journal_entry(self, entry) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None:
            return
        try:
            result = self._container.journals.save(
                selected.customer.id,
                entry,
                selected.customer.revision,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Journal konnte nicht gespeichert werden", str(exc))
        else:
            self._handle_journal_replay(result.replay)
        self._show_customer_detail(self.customer_list.currentRow())

    def edit_journal_entry(self, view) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None:
            return
        dialog = JournalEditorDialog(view.entry, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            result = self._container.journals.save(
                selected.customer.id,
                dialog.entry(),
                selected.customer.revision,
                local_key=view.key if view.entry.id is None else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Journal konnte nicht gespeichert werden", str(exc))
        else:
            self._handle_journal_replay(result.replay)
        self._show_customer_detail(self.customer_list.currentRow())

    def delete_journal_entry(self, view) -> None:
        selected = self._selected_customer()
        if selected is None or selected.customer.id is None:
            return
        try:
            if view.entry.id is None:
                self._container.journals.discard(view.key)
            else:
                result = self._container.journals.delete(
                    selected.customer.id,
                    view.entry.id,
                    selected.customer.revision,
                )
                self._handle_journal_replay(result.replay)
        except Exception as exc:
            QMessageBox.warning(self, "Journaleintrag konnte nicht gelöscht werden", str(exc))
        self._show_customer_detail(self.customer_list.currentRow())

    def _handle_journal_replay(self, replay) -> None:
        if not replay.conflicts:
            return
        for case in self._container.journals.conflicts():
            self._resolve_journal_conflict(case)

    def _resolve_journal_conflict(self, case) -> None:
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Gleichzeitige Journaländerung")
        message.setText(
            "Der Kunde wurde auf einem anderen Client geändert. "
            "Die lokale Journaländerung wurde nicht überschrieben."
        )
        reload_button = message.addButton("Neu laden", QMessageBox.ButtonRole.AcceptRole)
        merge_button = message.addButton(
            "Manuell zusammenführen", QMessageBox.ButtonRole.ActionRole
        )
        operation = getattr(getattr(case.mutation, "operation", None), "value", None)
        retry_label = "Erneut löschen" if operation == "delete" else "Erneut speichern"
        retry_button = message.addButton(retry_label, QMessageBox.ButtonRole.ActionRole)
        discard_button = message.addButton(
            "Lokale Änderung verwerfen", QMessageBox.ButtonRole.DestructiveRole
        )
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.exec()
        clicked = message.clickedButton()
        try:
            if clicked in {reload_button, discard_button}:
                self._container.journals.discard_conflict(case)
            elif clicked is retry_button:
                self._container.journals.retry_against_current(case)
            elif clicked is merge_button and case.local is not None:
                dialog = JournalEditorDialog(case.local, parent=self)
                if dialog.exec() == dialog.DialogCode.Accepted:
                    self._container.journals.retry_against_current(case, dialog.entry())
        except Exception as exc:
            QMessageBox.warning(self, "Journalkonflikt konnte nicht gelöst werden", str(exc))

    def _search_result_activated(self, hit) -> None:
        if not isinstance(hit, GlobalSearchHit):
            self.open_customer_page(hit)
            return
        if hit.record.kind is not GlobalSearchKind.CUSTOMER or hit.record.customer_id is None:
            return
        key = str(hit.record.customer_id)
        for view in self._customer_views:
            if view.key == key:
                self.open_customer_page(view.key)
                break

    def decide_customer_suggestion(self, suggestion, action: str, revision: int) -> None:
        if self._suggestion_write_busy:
            return
        self._suggestion_write_busy = True
        self._customer_detail_request_id += 1
        request_id = self._customer_detail_request_id
        self.customer_detail.set_suggestions_busy(True)
        reason = self.customer_detail.suggestion_rejection_reason(suggestion.id) if action == "reject" else ""

        def decide():
            try:
                result = self._container.review_gateway.decide_suggestion(
                    suggestion.customer_id, suggestion.id, action, revision,
                    **({"reason": reason} if reason else {}),
                )
                return suggestion.customer_id, request_id, suggestion.id, result, None
            except Exception as error:
                return suggestion.customer_id, request_id, suggestion.id, None, error

        task = BackgroundTask(decide)
        task.signals.succeeded.connect(self._apply_suggestion_decision)
        self._start_task(task)

    def _apply_suggestion_decision(self, outcome) -> None:
        customer_id, request_id, suggestion_id, result, error = outcome
        self._suggestion_write_busy = False
        if not self._review_result_is_current(customer_id, request_id):
            return
        self.customer_detail.set_suggestions_busy(False)
        if error is not None:
            if isinstance(error, ApiConflictError) and error.current is not None:
                self._replace_review_customer(error.current)
                self.customer_detail._suggestion_revision = error.current.revision
                self.customer_detail._update_suggestion_dialog()
            self.customer_detail.set_suggestions_error(str(error), self.customer_detail._suggestion_revision)
            QMessageBox.warning(self, "Vorschlag konnte nicht verarbeitet werden", str(error))
            return
        if result is None:
            self._show_customer_detail(self.customer_list.currentRow())
            return
        _decided_suggestion, customer = result
        self._replace_review_customer(customer)
        self.customer_detail.apply_suggestion_decision(suggestion_id, customer)
        self.load_suggestion_page(self.customer_detail._suggestion_offset)

    def _replace_review_customer(self, customer) -> None:
        self._customer_views = [replace(view, customer=customer)
                                if view.customer.id == customer.id else view
                                for view in self._customer_views]
        self.customer_detail.set_customer(customer)

    def _save_customer(self, customer, *, local_key=None) -> None:
        try:
            result = self._container.customers.save(customer, local_key=local_key)
        except Exception as exc:
            QMessageBox.warning(self, "Speichern fehlgeschlagen", str(exc))
            return
        self._handle_write_result(result)

    def _save_customer_from_detail(self, customer) -> None:
        selected = self._selected_customer()
        local_key = (
            selected.key
            if selected is not None and selected.customer.id is None
            else None
        )
        self._save_customer(customer, local_key=local_key)

    def _handle_write_result(self, result) -> None:
        self._handle_conflicts(result.replay.conflicts)
        self.refresh_customers()

    def _handle_conflicts(self, _conflicts) -> None:
        for case in self._container.customers.conflicts():
            self._resolve_conflict(case)

    def _resolve_conflict(self, case: ConflictCase) -> None:
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Gleichzeitige Kundenänderung")
        message.setText("Dieser Kunde wurde zwischenzeitlich auf einem anderen Client geändert.")
        message.setInformativeText(
            "Wähle bewusst, wie die lokale Änderung behandelt werden soll. Es wird nichts still überschrieben."
        )
        reload_button = message.addButton("Neu laden", QMessageBox.ButtonRole.AcceptRole)
        merge_button = message.addButton("Manuell zusammenführen", QMessageBox.ButtonRole.ActionRole)
        operation = getattr(getattr(case.mutation, "operation", None), "value", None)
        retry_label = "Erneut löschen" if operation == "delete" else "Erneut speichern"
        retry_button = message.addButton(retry_label, QMessageBox.ButtonRole.ActionRole)
        discard_button = message.addButton("Lokale Änderung verwerfen", QMessageBox.ButtonRole.DestructiveRole)
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.exec()
        clicked = message.clickedButton()
        try:
            if clicked is reload_button:
                self._container.customers.reload_from_server(case)
            elif clicked is discard_button:
                self._container.customers.discard_local(case)
            elif clicked is retry_button:
                self._container.customers.retry_against_current(case)
            elif clicked is merge_button and case.local is not None:
                server_name = case.current.display_name if case.current is not None else "nicht verfügbar"
                dialog = CustomerEditorDialog(
                    case.local,
                    explanation=f"Aktueller Serverwert: {server_name}. Passe die zusammengeführte Fassung an.",
                    parent=self,
                )
                if dialog.exec() == dialog.DialogCode.Accepted:
                    self._container.customers.save_merge(case, dialog.customer())
        except Exception as exc:
            QMessageBox.warning(self, "Konfliktlösung fehlgeschlagen", str(exc))

    def _selected_customer(self):
        row = self.customer_list.currentRow()
        return self._customer_views[row] if 0 <= row < len(self._customer_views) else None

    def _tab_changed(self, index: int) -> None:
        pages = (ClientPage.SEARCH, ClientPage.CUSTOMERS, ClientPage.CONNECTION)
        if 0 <= index < len(pages):
            self._container.navigation.navigate(pages[index])

    def _navigate(self, page: ClientPage) -> None:
        pages = (ClientPage.SEARCH, ClientPage.CUSTOMERS, ClientPage.CONNECTION)
        index = pages.index(page)
        self.tabs.setCurrentIndex(index)
        route = ("search", "customer", "connection")[index]
        if route == "customer" and self._selected_customer() is not None:
            self.navigator.navigate(route, self._selected_customer().key)
        else:
            self.navigator.navigate(route)

    def _start_task(self, task: BackgroundTask) -> None:
        self._tasks.add(task)
        task.signals.finished.connect(self._task_finished)
        self._pool.start(task)

    def _task_finished(self) -> None:
        sender = self.sender()
        self._tasks = {task for task in self._tasks if task.signals is not sender}

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._settings_saving:
            self.status_bar.set_text("Einstellungen werden noch gespeichert · bitte kurz warten …")
            event.ignore()
            return
        self._closing = True
        self._initial_index_timer.stop()
        self._recognition_poll.stop()
        self.search_debounce.stop()
        self._container.sync.stop()
        self.catalog_browser.shutdown()
        self.folder_page.cleanup()
        self._pool.clear()
        for task in tuple(self._tasks):
            try:
                task.signals.blockSignals(True)
            except (AttributeError, RuntimeError):
                pass
        self._pool.waitForDone(1_000)
        if self.tray_icon is not None:
            self.tray_icon.hide()
        super().closeEvent(event)


def forced_fullscreen() -> bool:
    return os.getenv("PAPAGUI_FORCE_FULLSCREEN", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def show_main_window(application: QApplication, window: ClientMainWindow) -> None:
    if not forced_fullscreen():
        window.show()
        return
    screen = window.screen() or application.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        window.setGeometry(geometry)
    window.show()
    if screen is not None:
        geometry = screen.availableGeometry()
        window.move(geometry.topLeft())
        window.resize(geometry.size())
    window.showFullScreen()


def apply_application_theme(application, selected_theme) -> None:
    """Apply the original palette while retaining the 0.4.2 theme contract."""
    manager = ThemeManager()
    value = getattr(selected_theme, "value", selected_theme)
    if str(value) in {"light", "dark"}:
        manager.set_mode(str(value))
    if hasattr(application, "setPalette"):
        manager.apply(application)
    else:
        application.setStyleSheet(theme_stylesheet(selected_theme, STYLE))


def run_main_gui(container: ClientContainer, *, automatic_sync: bool = True) -> int:
    set_process_identity("client")
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("PapaGUI Client")
    application.setWindowIcon(application_icon("client"))
    apply_application_theme(application, container.settings.theme)
    try:
        TrayProcessLauncher().start()
    except OSError:
        # The main client remains usable if the platform temporarily rejects a
        # detached process; users can still launch papagui-tray independently.
        pass
    window = ClientMainWindow(container, automatic_sync=automatic_sync)
    from papagui_client.updates.runtime import signal_ready

    show_main_window(application, window)
    QTimer.singleShot(0, signal_ready)
    application._papagui_window = window  # type: ignore[attr-defined]
    return application.exec()
