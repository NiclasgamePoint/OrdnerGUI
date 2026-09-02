from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QTreeWidgetItem
import pytest

from papagui_client.gui.viewers.catalog_browser import CatalogBrowserWidget
from papagui_client.gui.viewers.conversion_worker import FileConversionWorker
from papagui_client.gui.viewers.file import FileViewerWidget
from papagui_client.gui.viewers.image import ImageViewerWidget
from papagui_client.gui.viewers.pdf import PdfViewerWidget
from papagui_client.gui.viewers.spreadsheet import SpreadsheetViewerWidget, _column_label
from papagui_client.gui.viewers.text import TextViewerWidget
from papagui_client.viewers.models import ConversionOutcome, SheetPreview


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


class Worker:
    def __init__(self, converter=None, running=False):
        self.converter = converter
        self.running = running
        self.interrupted = 0
        self.cleaned = 0
        self.deleted = 0
        self.waited = 0

    def take_converter(self):
        value = self.converter
        self.converter = None
        return value

    def isRunning(self):
        return self.running

    def requestInterruption(self):
        self.interrupted += 1

    def cleanup(self):
        self.cleaned += 1

    def deleteLater(self):
        self.deleted += 1

    def wait(self):
        self.waited += 1


def test_file_viewer_missing_exception_xls_fallback_and_messages(application, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args))
    viewer = FileViewerWidget()
    assert not viewer.open_file(tmp_path / "missing.txt")
    assert warnings

    broken = tmp_path / "broken.txt"
    broken.write_text("value", encoding="utf-8")
    viewer._text_reader = SimpleNamespace(read=lambda _path: (_ for _ in ()).throw(OSError("bad")))
    assert viewer.open_file(broken)
    assert "bad" in viewer.text_viewer.editor.toPlainText()

    spreadsheet = tmp_path / "legacy.xls"
    spreadsheet.touch()
    viewer.spreadsheet_viewer.load = Mock(side_effect=ValueError("legacy"))
    conversions = []
    viewer._start_conversion = lambda *args: conversions.append(args)
    viewer.open_file(spreadsheet)
    assert conversions[-1][0] == FileConversionWorker.XLS_TO_XLSX

    modern = tmp_path / "modern.xlsx"
    modern.touch()
    with pytest.raises(ValueError):
        viewer._show_spreadsheet(modern)
    viewer.show_message("hello")
    assert viewer.text_viewer.editor.toPlainText() == "hello"
    viewer.close()


def test_file_viewer_conversion_completion_all_routes(application, tmp_path):
    viewer = FileViewerWidget()
    viewer._load_generation = 5
    worker = Worker(converter=SimpleNamespace(cleanup=lambda: None))
    viewer._on_conversion_completed(4, "anything", None, "", worker)
    viewer._on_conversion_completed(5, "anything", None, "abgebrochen", worker)

    errors = []
    viewer._handle_conversion_error = lambda *args: errors.append(args)
    viewer._on_conversion_completed(5, "word_to_pdf", None, "broken", worker)
    assert errors == [("word_to_pdf", "broken")]
    viewer._handle_conversion_error = FileViewerWidget._handle_conversion_error.__get__(viewer)

    viewer._on_conversion_completed(5, "unknown", None, "", worker)
    assert "kein Ergebnis" in viewer.text_viewer.editor.toPlainText()
    viewer._on_conversion_completed(
        5,
        FileConversionWorker.XLS_TO_XLSX,
        SimpleNamespace(path=None, converted=True, tool="tool", text=None),
        "",
        worker,
    )
    assert "keine XLSX" in viewer.text_viewer.editor.toPlainText()

    sheet = tmp_path / "sheet.xlsx"
    sheet.touch()
    viewer.spreadsheet_viewer.load = Mock()
    converter = SimpleNamespace(cleanup=lambda: None)
    worker = Worker(converter)
    outcome = ConversionOutcome("convert", "Excel", True, path=sheet)
    viewer._on_conversion_completed(5, FileConversionWorker.XLS_TO_XLSX, outcome, "", worker)
    assert viewer.stack.currentWidget() is viewer.spreadsheet_viewer
    assert viewer._active_converter is converter

    pdf = tmp_path / "render.pdf"
    pdf.touch()
    viewer.pdf_viewer.load = Mock()
    converter2 = SimpleNamespace(cleanup=lambda: None)
    worker = Worker(converter2)
    outcome = ConversionOutcome("word_to_pdf", "Word", True, path=pdf)
    viewer._on_conversion_completed(5, FileConversionWorker.WORD_TO_PDF, outcome, "", worker)
    assert viewer.stack.currentWidget() is viewer.pdf_viewer
    assert viewer._active_converter is converter2

    viewer._word_preview_error = "format failed"
    outcome = ConversionOutcome("extract_doc", "catdoc", False, text="plain text")
    viewer._on_conversion_completed(5, FileConversionWorker.EXTRACT_DOC, outcome, "", Worker())
    assert "format failed" in viewer.text_viewer.editor.toPlainText()
    empty = ConversionOutcome("extract_doc", "catdoc", False, text="")
    viewer._on_conversion_completed(5, FileConversionWorker.EXTRACT_DOC, empty, "", Worker())
    assert "nicht extrahiert" in viewer.text_viewer.editor.toPlainText()
    unknown = ConversionOutcome("convert", "tool", True, path=pdf)
    viewer._on_conversion_completed(5, "other", unknown, "", Worker())
    assert "Unbekannte" in viewer.text_viewer.editor.toPlainText()
    viewer.close()


def test_file_viewer_word_fallbacks_image_external_and_worker_cleanup(
    application, tmp_path, monkeypatch
):
    viewer = FileViewerWidget()
    docx = tmp_path / "letter.docx"
    docx.touch()
    viewer.current_file = docx
    viewer._docx_reader = SimpleNamespace(read=lambda _path: "fallback")
    viewer._handle_conversion_error(FileConversionWorker.WORD_TO_PDF, "format")
    assert "fallback" in viewer.text_viewer.editor.toPlainText()
    viewer._docx_reader = SimpleNamespace(
        read=lambda _path: (_ for _ in ()).throw(OSError("docx broken"))
    )
    viewer._handle_conversion_error(FileConversionWorker.WORD_TO_PDF, "format")
    assert "docx broken" in viewer.text_viewer.editor.toPlainText()

    doc = tmp_path / "letter.doc"
    doc.touch()
    viewer.current_file = doc
    calls = []
    viewer._start_conversion = lambda *args: calls.append(args)
    viewer._handle_conversion_error(FileConversionWorker.WORD_TO_PDF, "format")
    assert calls[-1][0] == FileConversionWorker.EXTRACT_DOC
    viewer.current_file = tmp_path / "unknown.bin"
    viewer._handle_conversion_error("other", "bad")
    assert "nicht konvertiert" in viewer.text_viewer.editor.toPlainText()

    invalid_image = tmp_path / "bad.png"
    invalid_image.write_bytes(b"not image")
    with pytest.raises(ValueError):
        viewer._show_image(invalid_image)

    open_results = iter((True, False))
    monkeypatch.setattr(
        "papagui_client.gui.viewers.file.QDesktopServices.openUrl",
        lambda _url: next(open_results),
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args))
    viewer._open_url(None, "Datei")
    viewer.current_file = doc
    viewer.open_externally()
    viewer.open_containing_folder()
    assert warnings

    worker = Worker(running=True)
    idle = Worker(running=False)
    viewer._conversion_workers = {worker, idle}
    assert viewer.is_converting
    viewer._cancel_conversions()
    assert worker.interrupted == 1 and idle.interrupted == 0
    viewer._release_conversion_worker(worker)
    assert worker.cleaned == 1 and worker.deleted == 1

    converter = SimpleNamespace(cleaned=0)
    converter.cleanup = lambda: setattr(converter, "cleaned", converter.cleaned + 1)
    viewer._active_converter = converter
    viewer._cleanup_active_converter()
    assert converter.cleaned == 1 and viewer._active_converter is None
    viewer._cleanup_active_converter()

    viewer._conversion_workers = {idle}
    viewer.shutdown()
    assert idle.waited == 1 and idle.cleaned == 1
    viewer.close()


def test_image_viewer_scaling_resize_and_control_wheel(application):
    viewer = ImageViewerWidget()
    viewer._update_display_pixmap()
    empty = QPixmap()
    viewer.set_image(empty)
    pixmap = QPixmap(100, 50)
    pixmap.fill(Qt.GlobalColor.red)
    viewer.resize(50, 50)
    viewer.set_image(pixmap)
    assert viewer._image_label.pixmap().width() <= 100

    class Wheel:
        def __init__(self, delta):
            self.delta = delta
            self.accepted = False

        def modifiers(self):
            return Qt.KeyboardModifier.ControlModifier

        def angleDelta(self):
            return QPoint(0, self.delta)

        def accept(self):
            self.accepted = True

    up = Wheel(120)
    viewer.wheelEvent(up)
    assert up.accepted and viewer._zoom_multiplier > 1
    down = Wheel(-120)
    viewer.wheelEvent(down)
    zero = Wheel(0)
    viewer.wheelEvent(zero)
    viewer.resize(60, 40)
    viewer.resizeEvent(SimpleNamespace()) if False else None
    viewer.close()


def test_pdf_viewer_invalid_navigation_zoom_search_and_close(application, tmp_path):
    viewer = PdfViewerWidget()
    invalid = tmp_path / "bad.pdf"
    invalid.write_bytes(b"bad")
    with pytest.raises(ValueError):
        viewer.load(invalid)

    viewer.document = SimpleNamespace(pageCount=lambda: 0)
    viewer._change_page(1)

    class Navigator:
        def currentPage(self):
            return 1

        def currentZoom(self):
            return 2.0

        def jump(self, *args):
            self.jumped = args

    navigator = Navigator()
    viewer.document = SimpleNamespace(pageCount=lambda: 3)
    viewer.view = SimpleNamespace(pageNavigator=lambda: navigator)
    viewer._change_page(9)
    assert navigator.jumped[0] == 2
    viewer._change_page(-9)
    assert navigator.jumped[0] == 0

    class View:
        def __init__(self):
            self.factor = 1.0
            self.index = -1

        def setZoomMode(self, value):
            self.mode = value

        def zoomFactor(self):
            return self.factor

        def setZoomFactor(self, value):
            self.factor = value

        def currentSearchResultIndex(self):
            return self.index

        def setCurrentSearchResultIndex(self, value):
            self.index = value

    view = View()
    viewer.view = view
    viewer._zoom(100)
    assert view.factor == 5.0
    view.factor = 1
    viewer._zoom(0.01)
    assert view.factor == 0.2
    search = SimpleNamespace(
        value="",
        setSearchString=lambda value: setattr(search, "value", value),
        count=lambda: 2,
    )
    viewer.search_model = search
    viewer.search_input.setText("")
    viewer.find_next()
    viewer.search_input.setText("needle")
    viewer.find_next()
    assert search.value == "needle" and view.index == 0
    search.count = lambda: 0
    viewer.find_next()
    viewer.close()


class SpreadsheetReader:
    def __init__(self, names=("Data",), preview=None):
        self.names = names
        self.preview = preview or SheetPreview(
            "Data",
            ((1.0, None), ("needle", 2.5)),
            total_rows=10,
            total_columns=4,
            maximum_rows=2,
            maximum_columns=2,
        )

    def sheet_names(self, _path):
        return self.names

    def read_sheet(self, *_args):
        return self.preview


def test_spreadsheet_widget_empty_load_render_and_search(application, tmp_path):
    assert _column_label(1) == "A"
    assert _column_label(26) == "Z"
    assert _column_label(27) == "AA"
    empty = SpreadsheetViewerWidget(SpreadsheetReader(names=()))
    with pytest.raises(ValueError):
        empty.load(tmp_path / "empty.xlsx")
    empty._load_current_sheet()

    viewer = SpreadsheetViewerWidget(SpreadsheetReader())
    viewer.load(tmp_path / "book.xlsx")
    assert viewer.table.item(0, 0).text() == "1"
    assert viewer.table.item(0, 1).text() == ""
    assert "begrenzt" in viewer.info_label.text()
    viewer.search_input.setText("")
    viewer.find_next()
    viewer.search_input.setText("needle")
    viewer.find_next()
    assert viewer.table.currentItem().text() == "needle"
    viewer.search_input.setText("absent")
    viewer.find_next()
    viewer.path = None
    viewer._load_current_sheet()
    viewer.close()


def test_text_search_wrap_and_catalog_empty_selection_activation(application, tmp_path):
    text = TextViewerWidget()
    text.set_text("one two one")
    text.search_input.clear()
    text.find_next()
    text.search_input.setText("one")
    text.find_next()
    text.find_next()
    text.find_next()  # wraps to the first occurrence
    assert text.editor.textCursor().selectedText() == "one"

    viewer = FileViewerWidget()
    viewer.show_message = Mock()
    viewer.open_file = Mock()
    viewer.shutdown = Mock()
    browser = CatalogBrowserWidget(SimpleNamespace(search=lambda _query: []), viewer=viewer)
    browser.run_search()
    assert browser.status_label.text() == "Keine Treffer gefunden"
    assert browser.selected_hit() is None
    browser.preview_selected()
    browser._emit_activated(QTreeWidgetItem(), 0)
    browser._hits = []
    item = QTreeWidgetItem(["bad"])
    item.setData(0, Qt.ItemDataRole.UserRole, 9)
    browser.results.addTopLevelItem(item)
    browser.results.setCurrentItem(item)
    assert browser.selected_hit() is None
    browser.shutdown()
    viewer.shutdown.assert_called_once()
    browser.close()
    text.close()
