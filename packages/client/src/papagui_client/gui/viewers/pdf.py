"""Searchable PDF preview with safe document replacement."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF
from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .buttons import viewer_button


class PdfViewerWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerContent")
        self.document = QPdfDocument(self)
        self._empty_document = QPdfDocument(self)
        self.search_model = QPdfSearchModel(self)
        self.search_model.setDocument(self.document)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.previous_button = viewer_button("‹", compact=True)
        self.next_button = viewer_button("›", compact=True)
        self.page_label = QLabel("Seite 0 / 0")
        self.zoom_out_button = viewer_button("−", compact=True)
        self.zoom_in_button = viewer_button("+", compact=True)
        self.fit_button = viewer_button("Breite")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Im PDF suchen …")
        self.search_input.setMaximumWidth(210)
        self.search_button = viewer_button("Weiter")
        for widget in (
            self.previous_button,
            self.next_button,
            self.page_label,
            self.zoom_out_button,
            self.zoom_in_button,
            self.fit_button,
        ):
            toolbar.addWidget(widget)
        toolbar.addStretch()
        toolbar.addWidget(self.search_input)
        toolbar.addWidget(self.search_button)
        layout.addLayout(toolbar)

        self.view = QPdfView()
        self.view.setObjectName("PdfView")
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.view.setSearchModel(self.search_model)
        layout.addWidget(self.view, 1)

        self.previous_button.clicked.connect(lambda: self._change_page(-1))
        self.next_button.clicked.connect(lambda: self._change_page(1))
        self.zoom_out_button.clicked.connect(lambda: self._zoom(0.8))
        self.zoom_in_button.clicked.connect(lambda: self._zoom(1.25))
        self.fit_button.clicked.connect(
            lambda: self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        )
        self.search_input.returnPressed.connect(self.find_next)
        self.search_button.clicked.connect(self.find_next)
        self.view.pageNavigator().currentPageChanged.connect(self._update_page_label)

    def load(self, path: Path) -> None:
        self.close_document()
        error = self.document.load(str(path))
        if error != QPdfDocument.Error.None_:
            self.document.close()
            raise ValueError(f"PDF konnte nicht geladen werden ({error.name}).")
        self.search_model.setDocument(self.document)
        self.view.setDocument(self.document)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._update_page_label(0)

    def _update_page_label(self, page: int) -> None:
        count = self.document.pageCount()
        self.page_label.setText(f"Seite {min(page + 1, count)} / {count}")
        self.previous_button.setEnabled(page > 0)
        self.next_button.setEnabled(page + 1 < count)

    def _change_page(self, delta: int) -> None:
        count = self.document.pageCount()
        if count < 1:
            return
        navigator = self.view.pageNavigator()
        target = max(0, min(count - 1, navigator.currentPage() + delta))
        navigator.jump(target, QPointF(0, 0), navigator.currentZoom())

    def _zoom(self, factor: float) -> None:
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(max(0.2, min(5.0, self.view.zoomFactor() * factor)))

    def find_next(self) -> None:
        query = self.search_input.text().strip()
        self.search_model.setSearchString(query)
        count = self.search_model.count()
        if not query or not count:
            return
        index = (self.view.currentSearchResultIndex() + 1) % count
        self.view.setCurrentSearchResultIndex(index)

    def close_document(self) -> None:
        self.search_model.setSearchString("")
        self.view.setDocument(self._empty_document)
        self.search_model.setDocument(self._empty_document)
        old_document = self.document
        old_document.close()
        old_document.deleteLater()
        QCoreApplication.sendPostedEvents(old_document, QEvent.Type.DeferredDelete)
        application = QApplication.instance()
        if application is not None:
            application.processEvents()
        self.document = QPdfDocument(self)
        self._update_page_label(0)
