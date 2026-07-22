from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QFileInfo, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.config import SETTINGS_APP, SETTINGS_ORG
from app.gui.panels import MailPanel
from app.gui.viewer import FileViewer
from app.gui.widgets.buttons import AppButton


class FolderTreeWidget(QTreeWidget):
    """Tree widget with the legacy count helper used by UI tests."""

    def count(self) -> int:
        return self.topLevelItemCount()


class FolderPage(QWidget):
    """Folder file list and reusable document viewer."""

    backRequested = Signal()
    openPathRequested = Signal(str)
    manageCustomerRequested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FolderPage")
        self.folder_path = ""
        self._all_files: list[dict] = []
        self._subfolders: list[dict] = []
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._icon_provider = QFileIconProvider()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("PageSplitter")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_file_card())
        self.splitter.addWidget(self._build_viewer_card())
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        saved_sizes = self._settings.value("ui/folder_splitter_sizes", [])
        if isinstance(saved_sizes, list) and len(saved_sizes) == 2:
            self.splitter.setSizes([int(value) for value in saved_sizes])
        else:
            self.splitter.setSizes([420, 680])
        self.splitter.splitterMoved.connect(self._save_splitter_sizes)
        layout.addWidget(self.splitter)

    def _build_file_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 16)
        layout.setSpacing(9)

        header = QHBoxLayout()
        back_button = AppButton("←", AppButton.SECONDARY, minimum_width=48)
        back_button.setObjectName("BackButton")
        back_button.clicked.connect(self.backRequested.emit)
        self.folder_title = QLabel("Dateien im Ordner")
        self.folder_title.setObjectName("PageTitle")
        header.addWidget(back_button)
        header.addWidget(self.folder_title, 1)
        layout.addLayout(header)

        self.folder_meta = QLabel("")
        self.folder_meta.setObjectName("PageSubtitle")
        self.folder_meta.setWordWrap(True)
        layout.addWidget(self.folder_meta)

        actions = QHBoxLayout()
        self.open_folder_button = AppButton("Ordner öffnen", AppButton.SECONDARY)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.customer_button = AppButton("Kundendaten", AppButton.SECONDARY)
        self.customer_button.clicked.connect(self._manage_customer)
        actions.addWidget(self.open_folder_button)
        actions.addWidget(self.customer_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        filter_row = QHBoxLayout()
        self.file_filter = QLineEdit()
        self.file_filter.setPlaceholderText("Dateien in diesem Ordner filtern …")
        self.file_filter.setClearButtonEnabled(True)
        self.file_filter.textChanged.connect(self._apply_file_filter)
        self.file_type_filter = QComboBox()
        self.file_type_filter.setMinimumWidth(150)
        self.file_type_filter.currentIndexChanged.connect(self._apply_file_filter)
        filter_row.addWidget(self.file_filter, 1)
        filter_row.addWidget(self.file_type_filter)
        layout.addLayout(filter_row)

        self.file_list = FolderTreeWidget()
        self.file_list.setObjectName("FolderFileList")
        self.file_list.setHeaderLabels(["Ordner und Dateien", "Details"])
        self.file_list.setColumnWidth(0, 330)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setMouseTracking(True)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._open_tree_context_menu)
        self.file_list.itemClicked.connect(self._open_selected_file)
        self.file_tabs = QTabWidget()
        self.file_tabs.addTab(self.file_list, "Ordner")
        self.mail_panel = MailPanel()
        self.file_tabs.addTab(self.mail_panel, "Mails")
        layout.addWidget(self.file_tabs, 1)
        return card

    def _build_viewer_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("PageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 16)
        title = QLabel("Datei-Viewer")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        self.file_viewer = FileViewer()
        layout.addWidget(self.file_viewer, 1)
        return card

    def set_folder(self, details: dict):
        self.folder_path = str(details.get("folder_path") or "")
        self._all_files = list(details.get("files") or [])
        self._subfolders = list(details.get("subfolders") or [])
        self.folder_title.setText(str(details.get("folder_name") or "Ordner"))
        file_count = int(details.get("file_count") or 0)
        size_mb = float(details.get("total_size") or 0) / 1024 / 1024
        services = ", ".join(details.get("service_types") or [])
        meta = f"{file_count} Dateien · {size_mb:.2f} MB"
        if services:
            meta += f" · {services}"
        self.folder_meta.setText(meta)
        selected_type = self.file_type_filter.currentData()
        self.file_type_filter.blockSignals(True)
        self.file_type_filter.clear()
        self.file_type_filter.addItem("Alle Dateitypen", "")
        file_types = sorted({
            str(item.get("file_type") or Path(str(item.get("filename") or "")).suffix.lstrip(".")).lower()
            for item in self._all_files
            if item.get("file_type") or Path(str(item.get("filename") or "")).suffix
        })
        for file_type in file_types:
            self.file_type_filter.addItem(file_type.upper(), file_type)
        self.file_type_filter.setCurrentIndex(
            max(0, self.file_type_filter.findData(selected_type))
        )
        self.file_type_filter.blockSignals(False)
        self.file_filter.clear()
        self._apply_file_filter("")

    def _apply_file_filter(self, _value=None):
        normalized = self.file_filter.text().strip().casefold()
        selected_type = str(self.file_type_filter.currentData() or "")
        self.file_list.clear()
        folder_items: dict[str, QTreeWidgetItem] = {}

        def append_folder(node: dict, parent: QTreeWidgetItem | None = None):
            item = QTreeWidgetItem([str(node.get("name") or "Ordner"), "Ordner"])
            path = str(node.get("path") or "")
            item.setData(0, Qt.UserRole, path)
            item.setData(0, Qt.UserRole + 1, "folder")
            item.setToolTip(0, path)
            if path:
                item.setIcon(0, self._icon_provider.icon(QFileInfo(path)))
            if parent is None:
                self.file_list.addTopLevelItem(item)
            else:
                parent.addChild(item)
            folder_items[self._path_key(path)] = item
            for child in node.get("children") or []:
                append_folder(child, item)

        for node in self._subfolders:
            append_folder(node)

        for file_info in self._all_files:
            filename = str(file_info.get("filename") or "")
            relative_dir = str(file_info.get("relative_dir") or "")
            file_type = str(
                file_info.get("file_type")
                or Path(filename).suffix.lower().lstrip(".")
            ).lower()
            if normalized and normalized not in filename.casefold() and normalized not in relative_dir.casefold():
                continue
            if selected_type and file_type != selected_type:
                continue
            details = [self._format_size(int(file_info.get("file_size") or 0))]
            modified = str(file_info.get("modified_date") or "")
            if modified:
                try:
                    modified = datetime.fromisoformat(modified).strftime("%d.%m.%Y")
                except ValueError:
                    pass
                details.append(modified)
            item = QTreeWidgetItem([filename, " · ".join(details)])
            path = str(file_info.get("path") or "")
            item.setData(0, Qt.UserRole, path)
            item.setData(0, Qt.UserRole + 1, "file")
            item.setToolTip(0, path)
            if path:
                item.setIcon(0, self._icon_provider.icon(QFileInfo(path)))
            parent = folder_items.get(self._path_key(str(Path(path).parent)))
            if parent is None:
                self.file_list.addTopLevelItem(item)
            else:
                parent.addChild(item)

        filters_active = bool(normalized or selected_type)

        def prune(item: QTreeWidgetItem) -> bool:
            child_visible = False
            for index in reversed(range(item.childCount())):
                child = item.child(index)
                if child.data(0, Qt.UserRole + 1) == "folder":
                    visible = prune(child)
                else:
                    visible = True
                child.setHidden(not visible)
                child_visible = child_visible or visible
            own_match = normalized in item.text(0).casefold() if normalized else False
            visible = not filters_active or child_visible or own_match
            item.setHidden(not visible)
            return visible

        for index in range(self.file_list.topLevelItemCount()):
            root_item = self.file_list.topLevelItem(index)
            if root_item.data(0, Qt.UserRole + 1) == "folder":
                prune(root_item)
        self.file_list.expandToDepth(0)

    @staticmethod
    def _format_size(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} B"

    def _open_selected_file(self, item: QTreeWidgetItem):
        path = str(item.data(0, Qt.UserRole) or "")
        if path and item.data(0, Qt.UserRole + 1) == "file":
            self.file_viewer.open_file(Path(path))

    def _open_tree_context_menu(self, position):
        item = self.file_list.itemAt(position)
        if item is None:
            return
        item_type = str(item.data(0, Qt.UserRole + 1) or "")
        path = str(item.data(0, Qt.UserRole) or "")

        menu = QMenu(self.file_list)
        open_action = menu.addAction("Oeffnen/Anzeigen")
        open_folder_action = menu.addAction("Ordner im System oeffnen")
        copy_action = menu.addAction("Pfad kopieren")
        open_folder_action.setEnabled(bool(path))
        copy_action.setEnabled(bool(path))

        selected = menu.exec(self.file_list.viewport().mapToGlobal(position))
        if selected == open_action:
            if item_type == "file":
                self._open_selected_file(item)
            elif path:
                self.openPathRequested.emit(path)
            return
        if selected == open_folder_action and path:
            self.openPathRequested.emit(path)
            return
        if selected == copy_action and path:
            QApplication.clipboard().setText(path)

    def _open_folder(self):
        if self.folder_path:
            self.openPathRequested.emit(self.folder_path)

    def _manage_customer(self):
        if self.folder_path:
            self.manageCustomerRequested.emit(
                self.folder_path,
                self.folder_title.text(),
            )

    def _save_splitter_sizes(self):
        if self.splitter.orientation() == Qt.Horizontal:
            self._settings.setValue("ui/folder_splitter_sizes", self.splitter.sizes())

    @staticmethod
    def _path_key(path: str) -> str:
        if not path:
            return ""
        return os.path.normcase(os.path.normpath(path))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = Qt.Vertical if self.width() < 920 else Qt.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)

    def cleanup(self):
        self.file_viewer.shutdown()
