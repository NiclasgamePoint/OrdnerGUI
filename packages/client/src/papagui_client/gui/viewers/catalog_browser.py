"""Global read-only catalog search, folder navigation, and document preview."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PySide6.QtCore import Qt, QStringListModel, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from papagui_client.application.models import (
    CatalogFolderDetails,
    CatalogFolderHit,
    CatalogHit,
    CatalogProjectRootHit,
    CatalogQuery,
    GlobalSearchHit,
    GlobalSearchKind,
    GlobalSearchPage,
    GlobalSearchQuery,
    GlobalSearchRecord,
    GlobalSearchSort,
)

from .file import FileViewerWidget


class CatalogSearch(Protocol):
    def search(self, query: CatalogQuery | str = "", **filters: object) -> list[CatalogHit]: ...

    def global_search(self, query: GlobalSearchQuery) -> GlobalSearchPage: ...

    def facets(self, source_id: str | None = None): ...

    def folders(self, source_id: str | None = None, **filters): ...

    def folder(self, source_id: str, relative_path: str) -> CatalogFolderDetails: ...

    def project_roots(self, source_id: str | None = None): ...

    def history(self) -> tuple[str, ...]: ...


SearchResult = CatalogHit | GlobalSearchHit


class CatalogBrowserWidget(QWidget):
    """Browse every immutable client snapshot without an index-writer."""

    resultActivated = Signal(object)
    PAGE_SIZE = 100

    def __init__(
        self,
        search: CatalogSearch,
        *,
        viewer: FileViewerWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._search = search
        self._hits: list[SearchResult] = []
        self._page: GlobalSearchPage | None = None
        self._history_model = QStringListModel(self)
        self.setObjectName("CatalogBrowser")

        layout = QVBoxLayout(self)
        filters = QFormLayout()
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Datei, Ordner, Projekt oder Kunde suchen …")
        self.query_completer = QCompleter(self._history_model, self)
        self.query_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.query_input.setCompleter(self.query_completer)
        filters.addRow("Globale Suche", self.query_input)

        detail_row = QHBoxLayout()
        self.kind_filter = QComboBox()
        for label, kind in (
            ("Alles", ""),
            ("Dokumente", GlobalSearchKind.DOCUMENT.value),
            ("Ordner", GlobalSearchKind.FOLDER.value),
            ("Projekte", GlobalSearchKind.PROJECT.value),
            ("Kunden", GlobalSearchKind.CUSTOMER.value),
        ):
            self.kind_filter.addItem(label, kind)
        self.customer_input = QLineEdit()
        self.customer_input.setPlaceholderText("Kunde")
        self.domain_filter = QComboBox()
        self.domain_filter.addItem("Alle Bereiche", "")
        self.year_input = QLineEdit()
        self.year_input.setPlaceholderText("Jahr")
        self.year_input.setMaximumWidth(90)
        self.file_type_input = QComboBox()
        self.file_type_input.setEditable(True)
        self.file_type_input.addItem("", "")
        self.sort_filter = QComboBox()
        self.sort_filter.addItem("Relevanz", GlobalSearchSort.RELEVANCE.value)
        self.sort_filter.addItem("Zuletzt geändert", GlobalSearchSort.MODIFIED.value)
        self.sort_filter.addItem("Alphabetisch", GlobalSearchSort.NAME.value)
        for widget in (
            self.kind_filter,
            self.customer_input,
            self.domain_filter,
            self.year_input,
            self.file_type_input,
            self.sort_filter,
        ):
            detail_row.addWidget(widget)
        self.search_button = QPushButton("Suchen")
        detail_row.addWidget(self.search_button)
        filters.addRow("Filter", detail_row)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_tabs = QTabWidget()
        self.results = QTreeWidget()
        self.results.setHeaderLabels(["Name", "Typ", "Details", "Quelle", "Geändert"])
        self.results.setAlternatingRowColors(True)
        self.results.setRootIsDecorated(False)
        self.results.setAccessibleName("Globale lokale Suche")
        self.left_tabs.addTab(self.results, "Suchergebnisse")
        self.navigation = QTreeWidget()
        self.navigation.setHeaderLabels(["Projekt und Ordner", "Bereich", "Jahr"])
        self.navigation.setAccessibleName("Portable Ordnernavigation")
        self.left_tabs.addTab(self.navigation, "Ordner")
        splitter.addWidget(self.left_tabs)
        self.viewer = viewer or FileViewerWidget()
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        paging = QHBoxLayout()
        self.previous_button = QPushButton("← Zurück")
        self.next_button = QPushButton("Weiter →")
        self.page_label = QLabel("")
        paging.addWidget(self.previous_button)
        paging.addWidget(self.next_button)
        paging.addWidget(self.page_label)
        paging.addStretch()
        layout.addLayout(paging)
        self.status_label = QLabel("Suchbegriff eingeben oder Ordner öffnen")
        self.status_label.setObjectName("CatalogBrowserStatus")
        layout.addWidget(self.status_label)

        self.query_input.returnPressed.connect(self.run_search)
        self.search_button.clicked.connect(self.run_search)
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.results.itemSelectionChanged.connect(self.preview_selected)
        self.results.itemActivated.connect(self._emit_activated)
        self.navigation.itemActivated.connect(self._open_navigation_item)
        self._update_paging(None)
        self.refresh_metadata()

    @property
    def supports_global_search(self) -> bool:
        return callable(getattr(self._search, "global_search", None))

    def refresh_metadata(self) -> None:
        if not self.supports_global_search:
            return
        try:
            facets = self._search.facets()
            self._replace_combo(self.domain_filter, "Alle Bereiche", facets.domains)
            current_type = self.file_type_input.currentText()
            self.file_type_input.clear()
            self.file_type_input.addItem("", "")
            self.file_type_input.addItems(facets.file_types)
            self.file_type_input.setCurrentText(current_type)
            self._history_model.setStringList(list(self._search.history()))
            self.reload_navigation()
        except Exception:
            # No active local generation is a normal first-start state.
            return

    @staticmethod
    def _replace_combo(combo: QComboBox, empty_label: str, values) -> None:
        selected = combo.currentData()
        combo.clear()
        combo.addItem(empty_label, "")
        for value in values:
            combo.addItem(str(value), str(value))
        combo.setCurrentIndex(max(0, combo.findData(selected)))

    def reload_navigation(self) -> None:
        roots = tuple(self._search.project_roots())
        folders = tuple(self._search.folders())
        self.navigation.clear()
        project_items: dict[int, QTreeWidgetItem] = {}
        folder_items: dict[int, QTreeWidgetItem] = {}
        folder_paths = {
            (item.folder.source.source_id, item.folder.source.relative_path): item
            for item in folders
        }
        for root in roots:
            project = root.project
            item = QTreeWidgetItem(
                [project.customer_name, project.service_type, str(project.year)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, root)
            item.setToolTip(0, str(root.local_path))
            self.navigation.addTopLevelItem(item)
            project_items[project.id] = item
            matching = folder_paths.get(
                (project.source.source_id, project.source.relative_path)
            )
            if matching is not None:
                item.setData(0, Qt.ItemDataRole.UserRole, matching)
                folder_items[matching.folder.id] = item
        for hit in sorted(
            folders,
            key=lambda value: value.folder.source.relative_path.count("/"),
        ):
            folder = hit.folder
            if folder.id in folder_items:
                continue
            item = QTreeWidgetItem([folder.name, "Ordner", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, hit)
            item.setToolTip(0, str(hit.local_path))
            parent = folder_items.get(folder.parent_id or -1)
            if parent is None and folder.project_root_id is not None:
                parent = project_items.get(folder.project_root_id)
            if parent is None:
                self.navigation.addTopLevelItem(item)
            else:
                parent.addChild(item)
            folder_items[folder.id] = item
        self.navigation.expandToDepth(0)

    def run_search(self, _checked: bool = False) -> None:
        if self.supports_global_search:
            self._run_global(0)
            return
        self._run_legacy_document_search()

    def _run_global(self, offset: int) -> None:
        kind = str(self.kind_filter.currentData() or "")
        kinds = (GlobalSearchKind(kind),) if kind else ()
        try:
            query = GlobalSearchQuery(
                text=self.query_input.text().strip(),
                kinds=kinds,
                domain_folder=str(self.domain_filter.currentData() or "") or None,
                customer_name=self.customer_input.text().strip() or None,
                year=self.year_input.text().strip() or None,
                file_type=self.file_type_input.currentText().strip().lstrip(".") or None,
                sort=GlobalSearchSort(str(self.sort_filter.currentData())),
                limit=self.PAGE_SIZE,
                offset=max(0, offset),
            )
            page = self._search.global_search(query)
        except Exception as exc:
            self._show_search_error(exc)
            return
        self._page = page
        self._hits = list(page.items)
        self._populate_results()
        self._update_paging(page)
        self._history_model.setStringList(list(self._search.history()))
        self.status_label.setText(
            f"{page.total} Treffer" if page.total else "Keine Treffer gefunden"
        )

    def _run_legacy_document_search(self) -> None:
        try:
            query = CatalogQuery(
                text=self.query_input.text().strip(),
                customer_name=self.customer_input.text().strip() or None,
                year=self.year_input.text().strip() or None,
                file_type=self.file_type_input.currentText().strip().lstrip(".") or None,
                limit=500,
            )
            self._hits = list(self._search.search(query))
        except Exception as exc:
            self._show_search_error(exc)
            return
        self._page = None
        self._populate_results()
        self._update_paging(None)
        self.status_label.setText(
            f"{len(self._hits)} Treffer" if self._hits else "Keine Treffer gefunden"
        )

    def _show_search_error(self, error: Exception) -> None:
        self._hits = []
        self._page = None
        self.results.clear()
        self._update_paging(None)
        self.status_label.setText(f"Suche nicht verfügbar: {error}")
        self.viewer.show_message("Lokaler Katalog konnte nicht gelesen werden.")

    def _populate_results(self) -> None:
        self.results.clear()
        self.left_tabs.setCurrentWidget(self.results)
        for index, hit in enumerate(self._hits):
            if isinstance(hit, CatalogHit):
                record = hit.record
                values = (
                    record.filename,
                    "Dokument",
                    " · ".join(
                        value
                        for value in (record.customer_name, record.project_name, record.year)
                        if value
                    ),
                    record.source_id,
                    record.modified_date,
                )
                tooltip = str(hit.local_path)
            else:
                record = hit.record
                labels = {
                    GlobalSearchKind.DOCUMENT: "Dokument",
                    GlobalSearchKind.FOLDER: "Ordner",
                    GlobalSearchKind.PROJECT: "Projekt",
                    GlobalSearchKind.CUSTOMER: "Kunde",
                }
                values = (
                    record.title,
                    labels[record.kind],
                    record.subtitle,
                    record.source_id,
                    record.modified_date,
                )
                tooltip = str(hit.local_path or "")
            item = QTreeWidgetItem(list(values))
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            item.setToolTip(0, tooltip)
            self.results.addTopLevelItem(item)
        if self._hits:
            self.results.setCurrentItem(self.results.topLevelItem(0))

    def selected_hit(self) -> SearchResult | None:
        selected = self.results.selectedItems()
        if not selected:
            return None
        index = selected[0].data(0, Qt.ItemDataRole.UserRole)
        return (
            self._hits[index]
            if isinstance(index, int) and 0 <= index < len(self._hits)
            else None
        )

    def preview_selected(self) -> None:
        hit = self.selected_hit()
        if hit is None:
            return
        if isinstance(hit, GlobalSearchHit) and hit.record.kind is not GlobalSearchKind.DOCUMENT:
            self.viewer.show_message(self._non_document_description(hit))
            return
        local_path = hit.local_path
        if local_path is None:
            self.viewer.show_message("Für diese Quelle ist kein lokales Pfadmapping vorhanden.")
            return
        path = Path(str(local_path))
        if path.is_file():
            self.viewer.open_file(path)
        else:
            self.viewer.show_message(
                "Der Eintrag ist im lokalen Katalog vorhanden, aber unter dem "
                f"konfigurierten Pfad nicht erreichbar:\n\n{path}"
            )

    @staticmethod
    def _non_document_description(hit: GlobalSearchHit) -> str:
        record = hit.record
        source = str(hit.local_path or record.relative_path or "Keine Pfadangabe")
        return f"{record.title}\n\n{record.subtitle}\n\n{source}".strip()

    def _emit_activated(self, _item: QTreeWidgetItem, _column: int) -> None:
        hit = self.selected_hit()
        if hit is None:
            return
        if isinstance(hit, GlobalSearchHit) and hit.record.kind in {
            GlobalSearchKind.FOLDER,
            GlobalSearchKind.PROJECT,
        }:
            self.open_folder(hit.record.source_id, hit.record.relative_path)
        self.resultActivated.emit(hit)

    def _open_navigation_item(self, item: QTreeWidgetItem, _column: int) -> None:
        value = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(value, CatalogFolderHit):
            self.open_folder(
                value.folder.source.source_id,
                value.folder.source.relative_path,
            )
        elif isinstance(value, CatalogProjectRootHit):
            self.open_folder(
                value.project.source.source_id,
                value.project.source.relative_path,
            )

    def open_folder(self, source_id: str, relative_path: str) -> None:
        try:
            details = self._search.folder(source_id, relative_path)
        except Exception as exc:
            self._show_search_error(exc)
            return
        self._hits = [
            self._global_folder(child) for child in details.children
        ] + [self._global_document(document) for document in details.documents]
        self._page = None
        self._populate_results()
        self._update_paging(None)
        self.status_label.setText(
            f"{details.folder.folder.name}: {len(details.children)} Unterordner, "
            f"{len(details.documents)} Dokumente"
        )

    @staticmethod
    def _global_folder(hit: CatalogFolderHit) -> GlobalSearchHit:
        folder = hit.folder
        return GlobalSearchHit(
            GlobalSearchRecord(
                GlobalSearchKind.FOLDER,
                f"folder:{folder.source.source_id}:{folder.id}",
                folder.name,
                f"{folder.file_count} Dateien",
                folder.source.source_id,
                folder.source.relative_path,
                folder.last_modified or "",
                metadata={"folder": folder},
            ),
            hit.local_path,
        )

    @staticmethod
    def _global_document(hit: CatalogHit) -> GlobalSearchHit:
        record = hit.record
        return GlobalSearchHit(
            GlobalSearchRecord(
                GlobalSearchKind.DOCUMENT,
                record.document_key,
                record.filename,
                " · ".join(
                    value
                    for value in (record.customer_name, record.project_name, record.year)
                    if value
                ),
                record.source_id,
                record.relative_path,
                record.modified_date,
                metadata={"catalog_record": record},
            ),
            hit.local_path,
        )

    def previous_page(self) -> None:
        if self._page is not None and self._page.has_previous:
            self._run_global(max(0, self._page.offset - self._page.limit))

    def next_page(self) -> None:
        if self._page is not None and self._page.has_next:
            self._run_global(self._page.offset + self._page.limit)

    def _update_paging(self, page: GlobalSearchPage | None) -> None:
        self.previous_button.setEnabled(bool(page and page.has_previous))
        self.next_button.setEnabled(bool(page and page.has_next))
        if page is None or page.total == 0:
            self.page_label.setText("")
            return
        start = page.offset + 1
        end = min(page.total, page.offset + len(page.items))
        self.page_label.setText(f"{start}–{end} von {page.total}")

    def shutdown(self) -> None:
        self.viewer.shutdown()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
