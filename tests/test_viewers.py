from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import openpyxl
from PyPDF2 import PdfWriter
from PySide6.QtWidgets import QApplication

from app.gui.viewer import FileViewer


class ViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_pdf_text_and_multisheet_excel_controls(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf_path = root / "test.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=400)
            with pdf_path.open("wb") as handle:
                writer.write(handle)

            excel_path = root / "test.xlsx"
            workbook = openpyxl.Workbook()
            workbook.active.title = "Übersicht"
            workbook.active["A1"] = "Musterwert"
            workbook.create_sheet("Details")["B2"] = "Treffer"
            workbook.save(excel_path)

            text_path = root / "test.txt"
            text_path.write_text("eins zwei drei", encoding="utf-8")

            viewer = FileViewer()
            viewer.open_file(pdf_path)
            self.assertEqual(viewer.pdf_viewer.document.pageCount(), 1)
            viewer.open_file(excel_path)
            self.assertEqual(viewer.spreadsheet_viewer.sheet_combo.count(), 2)
            viewer.spreadsheet_viewer.sheet_combo.setCurrentText("Details")
            self.assertEqual(viewer.spreadsheet_viewer.table.item(1, 1).text(), "Treffer")
            viewer.open_file(text_path)
            viewer.text_viewer.search_input.setText("zwei")
            viewer.text_viewer.find_next()
            self.assertTrue(viewer.text_viewer.editor.textCursor().hasSelection())
            viewer.close()


if __name__ == "__main__":
    unittest.main()
