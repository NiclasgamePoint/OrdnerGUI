from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from app.gui.widgets.buttons import AppButton


class PdfViewerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.document = QPdfDocument(self)
        self.search_model = QPdfSearchModel(self)
        self.search_model.setDocument(self.document)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.previous_button = AppButton("‹", AppButton.SECONDARY, minimum_width=32)
        self.next_button = AppButton("›", AppButton.SECONDARY, minimum_width=32)
        self.page_label = QLabel("Seite 0 / 0")
        self.zoom_out_button = AppButton("−", AppButton.SECONDARY, minimum_width=32)
        self.zoom_in_button = AppButton("+", AppButton.SECONDARY, minimum_width=32)
        self.fit_button = AppButton("Breite", AppButton.SECONDARY)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Im PDF suchen …")
        self.search_input.setMaximumWidth(210)
        self.search_button = AppButton("Weiter", AppButton.SECONDARY)
        for widget in (
            self.previous_button, self.next_button, self.page_label,
            self.zoom_out_button, self.zoom_in_button, self.fit_button,
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
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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

    def load(self, path: Path):
        self.document.close()
        error = self.document.load(str(path))
        if error != QPdfDocument.Error.None_:
            raise ValueError(f"PDF konnte nicht geladen werden ({error.name}).")
        self.view.setDocument(self.document)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._update_page_label(0)

    def _update_page_label(self, page: int):
        count = self.document.pageCount()
        self.page_label.setText(f"Seite {min(page + 1, count)} / {count}")
        self.previous_button.setEnabled(page > 0)
        self.next_button.setEnabled(page + 1 < count)

    def _change_page(self, delta: int):
        navigator = self.view.pageNavigator()
        target = max(0, min(self.document.pageCount() - 1, navigator.currentPage() + delta))
        navigator.jump(target, QPointF(0, 0), navigator.currentZoom())

    def _zoom(self, factor: float):
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(max(0.2, min(5.0, self.view.zoomFactor() * factor)))

    def find_next(self):
        query = self.search_input.text().strip()
        self.search_model.setSearchString(query)
        count = self.search_model.count()
        if not query or not count:
            return
        index = (self.view.currentSearchResultIndex() + 1) % count
        self.view.setCurrentSearchResultIndex(index)

    def close_document(self):
        self.document.close()
