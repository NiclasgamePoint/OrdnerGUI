"""v0.4.1 desktop shell backed exclusively by 0.4.2 client services."""

from __future__ import annotations

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
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from papagui_client.composition import ClientContainer
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
from .legacy_models import ApplicationStatistics, SearchPreferences, SearchSort
from .launcher import TrayProcessLauncher
from .navigation import NavigationController
from .pages import FolderPage, SearchPage
from .settings import ClientSettingsDialog, theme_stylesheet
from .tasks import BackgroundTask
from .theme import ThemeManager, build_stylesheet
from .timers import QtTimerAdapter
from .widgets import AppHeader, IndexStatusBar, SearchFilterPopup


STYLE = build_stylesheet("light", "#2db89d", 100, 13)


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
        self._closing = False
        self._folder_routes: dict[object, tuple[str, str]] = {}
        self._last_search_page = None
        self._application_statistics = ApplicationStatistics()
        self.tray_icon: QSystemTrayIcon | None = None
        self.setWindowTitle("PapaGUI - Kundenmanagement System")
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
        self.header = AppHeader(history, document_search_enabled=True)
        layout.addWidget(self.header)

        self.filter_popup = SearchFilterPopup(self)
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
        self.search_page = SearchPage(document_search_enabled=True)
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
        self.folder_page.backRequested.connect(self.navigator.back)
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
            file_count=sum(hit.folder.file_count for hit in folders),
            total_file_size=sum(hit.folder.total_size for hit in folders),
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
            query = GlobalSearchQuery(
                text=query_text,
                domain_folder=str(self.domain_filter.currentData() or "") or None,
                year=str(self.year_filter.currentData() or "") or None,
                file_type=str(self.file_type_filter.currentData() or "") or None,
                sort=sort_map.get(
                    self.filter_popup.sort_combo.currentData(),
                    GlobalSearchSort.RELEVANCE,
                ),
                limit=500,
            )
            page = self._container.search.global_search(query)
        except AttributeError:
            self._run_document_only_search(query_text)
            return
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
        documents: list[dict] = []
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
                file_count = getattr(folder, "file_count", 0)
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
            documents.append(
                {
                    "filename": record.title,
                    "excerpt": record.subtitle,
                    "path": local_path,
                }
            )
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
        self.search_page.set_documents(
            documents,
            int(page.kind_counts.get(GlobalSearchKind.DOCUMENT.value, len(documents))),
        )
        try:
            self.header.set_history(list(self._container.search.history()))
        except Exception:
            pass
        self.status_bar.set_text(f"Suche abgeschlossen · {page.total} Treffer")

    def _run_document_only_search(self, query_text: str) -> None:
        try:
            hits = self._container.search.search(query_text)
        except Exception as exc:
            self.search_page.set_document_error(str(exc))
            return
        documents = [
            {
                "filename": hit.record.filename,
                "excerpt": hit.record.project_name,
                "path": str(hit.local_path),
            }
            for hit in hits
        ]
        customers = [
            view for view in self._customer_views
            if query_text.casefold() in view.customer.display_name.casefold()
        ]
        self.search_page.set_customers(
            [view.customer for view in customers],
            len(customers),
            keys=[view.key for view in customers],
            paths=[self._customer_local_path(view.customer) for view in customers],
        )
        self.search_page.set_folders([], 0)
        self.search_page.set_documents(documents, len(documents))

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
        for row, view in enumerate(self._customer_views):
            if view.key == normalized or (
                view.customer.id is not None and str(view.customer.id) == normalized
            ):
                self.customer_list.setCurrentRow(row)
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
        if isinstance(payload, tuple) and len(payload) == 2:
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
                if self.customer_list.currentRow() != row:
                    self.customer_list.setCurrentRow(row)
                else:
                    self._show_customer_detail(row)
                self.page_stack.setCurrentWidget(self.customer_page)
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
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self
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
        self._save_client_settings(dialog.settings())

    def open_onboarding(self) -> None:
        """Collect only the client-side values required for first use."""
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

    def _save_client_settings(self, settings, *, onboarding: bool = False) -> None:
        try:
            effective = self._container.save_client_settings(settings)
        except Exception as exc:
            QMessageBox.warning(self, "Einstellungen nicht gespeichert", str(exc))
            return
        application = QApplication.instance()
        if application is not None:
            apply_application_theme(application, effective.theme)
        self._refresh_connection_labels()
        self._refresh_search_metadata()
        self.status_bar.set_text(
            "Einrichtung abgeschlossen"
            if onboarding
            else "Client-Einstellungen gespeichert"
        )

    def synchronize(self) -> None:
        if self._syncing or self._closing:
            return
        self._syncing = True
        self.status_bar.set_text("Synchronisierung läuft …")
        task = BackgroundTask(self._container.sync.sync)
        task.signals.succeeded.connect(self._sync_complete)
        task.signals.failed.connect(self._sync_failed)
        task.signals.finished.connect(self._sync_finished)
        self._start_task(task)

    def _sync_complete(self, result) -> None:
        self.status_bar.set_text(
            "Synchronisiert · " + ", ".join(result.changed_components)
            if result.changed
            else "Synchronisiert · aktuell"
        )
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

    def _sync_failed(self, error: str) -> None:
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
        selected = self._customer_views[row] if 0 <= row < len(self._customer_views) else None
        customer = selected.customer if selected is not None else None
        summaries = self._customer_project_summaries(customer) if customer is not None else {}
        self.customer_detail.set_customer(customer, summaries)
        if customer is None or customer.id is None:
            self.customer_detail.set_journal(())
            self.customer_detail.set_suggestions((), 0)
            return
        try:
            journals = getattr(self._container, "journals", None)
            self.customer_detail.set_journal(
                journals.list(customer.id) if journals is not None else ()
            )
        except Exception:
            self.customer_detail.set_journal(())
        try:
            suggestions, revision = self._container.review_gateway.customer_suggestions(
                customer.id, "pending"
            )
            self.customer_detail.set_suggestions(suggestions, revision)
        except Exception:
            self.customer_detail.set_suggestions((), customer.revision)

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
        for row, view in enumerate(self._customer_views):
            if view.key == key:
                self.tabs.setCurrentIndex(1)
                self.customer_list.setCurrentRow(row)
                self.navigator.navigate("customer", view.key)
                break

    def decide_customer_suggestion(self, suggestion, action: str, revision: int) -> None:
        try:
            self._container.review_gateway.decide_suggestion(
                suggestion.customer_id,
                suggestion.id,
                action,
                revision,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Vorschlag konnte nicht verarbeitet werden", str(exc))
            return
        self._show_customer_detail(self.customer_list.currentRow())

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
        self._closing = True
        self.search_debounce.stop()
        self._container.sync.stop()
        self.catalog_browser.shutdown()
        self.folder_page.cleanup()
        self._pool.clear()
        for task in tuple(self._tasks):
            try:
                task.signals.disconnect()
            except RuntimeError:
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
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("PapaGUI Client")
    apply_application_theme(application, container.settings.theme)
    try:
        TrayProcessLauncher().start()
    except OSError:
        # The main client remains usable if the platform temporarily rejects a
        # detached process; users can still launch papagui-tray independently.
        pass
    window = ClientMainWindow(container, automatic_sync=automatic_sync)
    show_main_window(application, window)
    application._papagui_window = window  # type: ignore[attr-defined]
    return application.exec()
