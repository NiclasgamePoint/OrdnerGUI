from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QLabel,
    QScrollArea,
    QWidget,
)

from app.core.customer_models import Contact, Customer
from app.core.config import CustomerRecognitionOptions
from app.core.customer_repository import CustomerRepository
from app.core.search_models import RecentCustomerHistory
from app.core.statistics import ApplicationStatistics
from app.gui.dialogs.centered_popup import CenteredPopupDialog
from app.gui.dialogs.customer_editor import CustomerEditorDialog
from app.gui.navigation import NavigationController
from app.gui.pages import CustomerPage, FolderPage, SearchPage
from app.gui.settings_popup import SettingsPopup
from app.gui.widgets.search_filter_popup import SearchFilterPopup


class UiNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_navigation_controller_restores_previous_route(self):
        navigator = NavigationController()
        navigator.navigate("customer", 7)
        navigator.navigate("folder", "/tmp/example")
        self.assertTrue(navigator.can_go_back)

        navigator.back()
        self.assertEqual(navigator.current.page, "customer")
        self.assertEqual(navigator.current.payload, 7)

        navigator.back()
        self.assertEqual(navigator.current.page, "search")
        self.assertFalse(navigator.can_go_back)

    def test_search_filter_labels_year_filter_without_templates(self):
        popup = SearchFilterPopup()
        self.assertEqual(popup.year_combo.accessibleName(), "Jahre")
        self.assertNotIn("Vorlagen", popup.year_combo.accessibleDescription())
        popup.close()

    def test_folder_page_path_key_normalizes_pathlib_variants(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "Kunden" / "Projekt"
            project.mkdir(parents=True)
            with_parent_segment = project.parent / "Zwischenordner" / ".." / project.name
            with_duplicate_separator = f"{project.parent}//{project.name}"

            self.assertEqual(
                FolderPage._path_key(str(project)),
                FolderPage._path_key(str(with_parent_segment)),
            )
            self.assertEqual(
                FolderPage._path_key(str(project)),
                FolderPage._path_key(with_duplicate_separator),
            )

    def test_navigation_controller_supports_forward(self):
        navigator = NavigationController()
        navigator.back()
        navigator.forward()
        navigator.navigate("search")
        navigator.navigate("temporary", remember=False)
        self.assertFalse(navigator.can_go_back)
        navigator.reset()
        navigator.navigate("customer", 7)
        navigator.navigate("folder", "/tmp/example")
        navigator.back()
        self.assertTrue(navigator.can_go_forward)

        navigator.forward()
        self.assertEqual(navigator.current.page, "folder")
        self.assertEqual(navigator.current.payload, "/tmp/example")

    def test_centered_popup_is_modal_and_centered_over_owner(self):
        owner = QWidget()
        owner.setGeometry(40, 30, 700, 500)
        owner.show()
        popup = CenteredPopupDialog(owner)
        popup.resize(500, 360)
        popup.show()
        self.app.processEvents()
        popup.center_on_parent()

        owner_center = owner.mapToGlobal(owner.rect().center())
        popup_center = popup.frameGeometry().center()
        self.assertLessEqual(abs(owner_center.x() - popup_center.x()), 1)
        self.assertLessEqual(abs(owner_center.y() - popup_center.y()), 1)
        self.assertTrue(popup.isModal())
        self.assertTrue(popup.windowFlags() & Qt.FramelessWindowHint)

        popup.close()
        owner.close()

        unowned = CenteredPopupDialog()
        with patch(
            "app.gui.dialogs.centered_popup.QApplication.primaryScreen",
            return_value=None,
        ):
            unowned.center_on_parent()
        unowned.close()

    def test_search_page_separates_customer_and_folder_rows(self):
        page = SearchPage()
        customer = Customer(
            id=12,
            display_name="Muster GmbH",
            entity_type="Unternehmen",
            city="Hamburg",
            folder_path="/data/Muster GmbH",
            folder_paths=["/data/Muster GmbH"],
        )
        page.set_customers([customer], 1)
        page.set_folders(
            [{
                "folder_name": "Projekt A",
                "folder_path": "/data/Projekt A",
                "relative_path": "2026/Projekt A",
                "file_count": 4,
            }],
            1,
        )

        self.assertEqual(page.customer_section.row_count, 1)
        self.assertEqual(page.folder_section.row_count, 1)
        customer_labels = page.customer_section._rows[0].findChildren(QLabel)
        self.assertEqual(customer_labels[0].text(), "Muster GmbH · Hamburg")
        self.assertNotIn("Hamburg", customer_labels[1].text())

    def test_search_page_can_start_with_empty_customer_overview(self):
        page = SearchPage()

        page.reset([])

        self.assertEqual(page.customer_section.row_count, 0)
        self.assertEqual(page.customer_section._message.text(), "")

    def test_recent_customer_history_keeps_last_five_unique_ids(self):
        history = RecentCustomerHistory(maximum=5)
        history.clear()
        self.addCleanup(history.clear)

        history.remember([1, 2, 3])
        history.remember([3, 4, 5, 6])

        self.assertEqual(history.ids(), [3, 4, 5, 6, 1])

    def test_customer_editor_shows_compact_folder_context_but_keeps_full_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "Blower Door" / "2026" / "Musterkunde"
            project.mkdir(parents=True)
            repository = CustomerRepository(root / "customers.db")
            customer = repository.save(Customer(
                display_name="Musterkunde",
                company="Musterkunde",
                folder_path=str(project),
                folder_paths=[str(project)],
            ))

            dialog = CustomerEditorDialog(
                repository,
                customer_id=customer.id,
            )
            name_item = dialog.selected_folders_table.item(0, 0)
            service_item = dialog.selected_folders_table.item(0, 1)
            year_item = dialog.selected_folders_table.item(0, 2)

            self.assertEqual(name_item.text(), "Musterkunde")
            self.assertEqual(service_item.text(), "Blower Door")
            self.assertEqual(year_item.text(), "2026")
            self.assertEqual(name_item.data(Qt.UserRole), str(project.resolve()))
            self.assertIn(str(project.resolve()), name_item.toolTip())
            self.assertNotIn(str(root), dialog.folder_paths.text())

            dialog.selected_folders_table.itemClicked.emit(service_item)
            self.assertEqual(dialog.selected_folders_table.rowCount(), 0)
            self.assertEqual(dialog.found_folders_table.rowCount(), 1)
            self.assertEqual(
                dialog.found_folders_table.item(0, 0).data(Qt.UserRole),
                str(project.resolve()),
            )

            dialog.found_folders_table.itemClicked.emit(
                dialog.found_folders_table.item(0, 2)
            )
            self.assertEqual(dialog.selected_folders_table.rowCount(), 1)
            self.assertEqual(dialog.found_folders_table.rowCount(), 0)

            dialog.close()
            repository.close()

    def test_customer_editor_assigns_current_folder_to_existing_customer(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "Energieberatung" / "2026" / "Projekt B"
            folder.mkdir(parents=True)
            repository = CustomerRepository(root / "customers.db")
            customer = repository.save(Customer(
                display_name="Bestandskunde",
                company="Bestandskunde",
            ))

            dialog = CustomerEditorDialog(
                repository,
                folder_path=str(folder),
                suggested_name="Projekt B",
            )
            self.assertIsNotNone(dialog.existing_customer_combo)
            dialog.existing_customer_combo.setCurrentIndex(0)
            dialog._assign_to_existing_customer()

            assigned = repository.get_by_folder(str(folder))
            self.assertIsNotNone(assigned)
            self.assertEqual(assigned.id, customer.id)
            self.assertIn("Energieberatung", assigned.service_types)

            dialog.close()
            repository.close()

    def test_customer_editor_contacts_table_uses_editable_row_height(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            repository = CustomerRepository(root / "customers.db")
            dialog = CustomerEditorDialog(repository, suggested_name="Muster")

            self.assertGreaterEqual(
                dialog.contacts_table.verticalHeader().defaultSectionSize(),
                34,
            )

            dialog.close()
            repository.close()

    def test_customer_editor_edits_and_saves_contact_role_and_column_order(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(
                Customer(
                    display_name="Muster GmbH",
                    company="Muster GmbH",
                    entity_type="Unternehmen",
                    contacts=[
                        Contact(
                            name="Erika Muster",
                            role="Geschäftsführung",
                            phone="01234 567890",
                            email="erika@example.de",
                        )
                    ],
                )
            )
            dialog = CustomerEditorDialog(repository, customer_id=customer.id)

            headers = [
                dialog.contacts_table.horizontalHeaderItem(column).text()
                for column in range(dialog.contacts_table.columnCount())
            ]
            self.assertEqual(headers, ["Name", "Rolle", "Telefon", "E-Mail"])
            header = dialog.contacts_table.horizontalHeader()
            self.assertEqual(header.sectionResizeMode(2), QHeaderView.ResizeToContents)
            self.assertEqual(header.sectionResizeMode(3), QHeaderView.Stretch)
            self.assertFalse(dialog.contacts_table.isColumnHidden(1))
            self.assertEqual(dialog.contacts_table.item(0, 1).text(), "Geschäftsführung")
            self.assertEqual(dialog.contacts_table.item(0, 2).text(), "01234 567890")
            self.assertEqual(dialog.contacts_table.item(0, 3).text(), "erika@example.de")

            dialog.contacts_table.item(0, 1).setText("Bauleitung")
            dialog._save()
            saved = repository.get(int(customer.id))
            self.assertEqual(saved.contacts[0].role, "Bauleitung")
            self.assertEqual(saved.contacts[0].phone, "01234 567890")
            self.assertEqual(saved.contacts[0].email, "erika@example.de")

            dialog.close()
            repository.close()

    def test_customer_editor_role_visibility_follows_customer_type(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            dialog = CustomerEditorDialog(repository, suggested_name="Muster")

            dialog._append_contact(Contact(name="Test", role="Leitung"))
            self.assertFalse(dialog.contacts_table.isColumnHidden(1))
            dialog.entity_type.setCurrentText("Privatperson")
            self.assertTrue(dialog.contacts_table.isColumnHidden(1))
            dialog.entity_type.setCurrentText("Unternehmen")
            self.assertFalse(dialog.contacts_table.isColumnHidden(1))
            self.assertEqual(dialog.contacts_table.item(0, 1).text(), "Leitung")

            dialog.close()
            repository.close()

    def test_customer_page_lists_services_by_newest_project_first(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old_folder = root / "Blower Door" / "2023" / "Muster, Kiel"
            new_folder = root / "Baubegleitung" / "2025" / "Muster, Hamburg"
            repository = CustomerRepository(root / "customers.db")
            customer = repository.save(Customer(display_name="Muster"))
            repository.add_folder_to_customer(
                int(customer.id),
                str(old_folder),
                "Blower Door",
            )
            repository.add_folder_to_customer(
                int(customer.id),
                str(new_folder),
                "Baubegleitung",
            )

            page = CustomerPage(repository)
            page.set_customer(repository.get(int(customer.id)))

            row_titles = [
                row.findChildren(QLabel)[0].text()
                for row in page._folder_rows
            ]

            self.assertEqual(row_titles, [
                "Baubegleitung · 2025 · Hamburg",
                "Blower Door · 2023 · Kiel",
            ])
            row_subtitles = [
                row.findChildren(QLabel)[1].text()
                for row in page._folder_rows
            ]
            self.assertIn("Muster, Hamburg", row_subtitles[0])
            self.assertIn("Muster, Kiel", row_subtitles[1])
            self.assertFalse(page.folder_message.isVisible())
            page.close()
            repository.close()

    def test_search_page_shows_home_statistics_and_document_results(self):
        page = SearchPage(document_search_enabled=True)
        page.set_statistics(ApplicationStatistics(
            customer_count=7,
            project_count=12,
        ))
        page.reset([])
        self.assertFalse(page.statistics_widget.isHidden())
        card_layout = page.statistics_widget.parentWidget().layout()
        self.assertGreater(
            card_layout.indexOf(page.statistics_widget),
            card_layout.indexOf(page.scroll_area),
        )
        self.assertEqual(
            page.statistics_widget._value_labels["customer_count"].text(),
            "7",
        )
        opened_files: list[str] = []
        opened_paths: list[str] = []
        page.openFileRequested.connect(opened_files.append)
        page.openPathRequested.connect(opened_paths.append)
        page.prepare_search()
        page.set_documents([
            {
                "filename": "bericht.docx",
                "path": "/tmp/projekt/bericht.docx",
                "excerpt": "Passender Dokumentausschnitt",
            }
        ], 1)

        self.assertTrue(page.statistics_widget.isHidden())
        row = page.document_section._rows[0]
        row._open_file()
        row._open_folder()
        self.assertEqual(opened_files, ["/tmp/projekt/bericht.docx"])
        self.assertEqual(opened_paths, [str(Path("/tmp/projekt"))])
        self.assertIn("Dokumentausschnitt", row.snippet_label.text())
        page.close()

    def test_search_page_hides_document_results_by_default(self):
        page = SearchPage()
        page.prepare_search()
        page.set_documents(
            [{"filename": "verborgen.pdf", "path": "/tmp/verborgen.pdf"}],
            1,
        )

        self.assertTrue(page.document_section.isHidden())
        self.assertEqual(page.document_section.row_count, 0)
        self.assertEqual(
            page.scroll_area.accessibleDescription(),
            "Enthält Kunden- und Ordnertreffer.",
        )
        page.close()

    def test_search_page_errors_statistics_coverage_and_plain_rows(self):
        page = SearchPage(document_search_enabled=True)
        plain_row = QWidget()
        page.customer_section.set_rows([plain_row], 1)
        page.customer_section.set_note("Hinweis")
        self.assertEqual(page.customer_section._message.text(), "Hinweis")
        minimal_customer = Customer(display_name="Nur Name")
        page.reset([minimal_customer])
        page.set_customers([Customer(
            display_name="Mit Dienst", city="Ort", service_types=["Service"]
        )], 1)
        page.set_folders([{}], 1)
        page.show_short_query_hint()
        self.assertIn("Mindestens", page.document_section._message.text())
        page.set_customer_error("customer")
        page.set_folder_error("folder")
        page.set_document_error("document")
        self.assertIn("document", page.document_section._message.text())
        page.set_statistics(None)
        page.set_statistics(None, "statistics")

        page.set_document_coverage(None)
        page.set_document_coverage(SimpleNamespace(complete=True))
        page.set_document_coverage(SimpleNamespace(
            complete=False, completed_documents=0, total_documents=0,
            unavailable_shards=0,
        ))
        self.assertIn("0 %", page.document_section._message.text())
        page.set_document_coverage(SimpleNamespace(
            complete=False, completed_documents=1, total_documents=2,
            unavailable_shards=3,
        ))
        self.assertIn("3 Shards", page.document_section._message.text())

        page.set_document_search_enabled(False)
        page.show_short_query_hint()
        page.set_document_error("ignored")
        page.set_document_coverage(None)
        page.set_document_search_enabled(True)
        page.close()

    def test_central_widgets_expose_accessibility_metadata(self):
        page = SearchPage()
        self.assertTrue(page.scroll_area.accessibleName())
        self.assertTrue(page.scroll_area.accessibleDescription())
        self.assertTrue(page.statistics_widget.accessibleName())
        popup = SettingsPopup("light", "#2db89d")
        self.assertTrue(popup.nav_list.accessibleName())
        self.assertTrue(popup.nav_list.accessibleDescription())
        self.assertTrue(popup.data_path_input.accessibleName())
        popup.close()
        page.close()

    def test_folder_page_populates_only_matching_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "eins.pdf"
            second = root / "zwei.txt"
            first.write_bytes(b"%PDF")
            second.write_text("Text", encoding="utf-8")

            page = FolderPage()
            page.set_folder({
                "folder_path": str(root),
                "folder_name": "Testordner",
                "file_count": 2,
                "total_size": first.stat().st_size + second.stat().st_size,
                "service_types": ["DEKRA"],
                "files": [
                    {
                        "filename": first.name,
                        "path": str(first),
                        "relative_dir": "",
                    },
                    {
                        "filename": second.name,
                        "path": str(second),
                        "relative_dir": "",
                    },
                ],
            })
            self.assertEqual(page.file_tabs.count(), 2)
            self.assertEqual(page.file_tabs.objectName(), "InsetContentTabs")
            self.assertEqual(page.file_tabs.tabText(0), "Ordner")
            self.assertEqual(page.file_tabs.tabText(1), "Mails")
            self.assertTrue(page.file_list.hasMouseTracking())
            self.assertEqual(page.file_list.count(), 2)
            page.file_filter.setText("zwei")
            self.assertEqual(page.file_list.count(), 1)
            page.cleanup()

    def test_folder_page_context_open_uses_external_file_signal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "angebot.docx"
            document.write_bytes(b"document")
            page = FolderPage()
            page.set_folder({
                "folder_path": str(root),
                "folder_name": "Projekt",
                "file_count": 1,
                "files": [{
                    "filename": document.name,
                    "path": str(document),
                    "relative_dir": "",
                }],
            })
            opened_files: list[str] = []
            opened_paths: list[str] = []
            page.openFileRequested.connect(opened_files.append)
            page.openPathRequested.connect(opened_paths.append)

            file_item = page.file_list.topLevelItem(0)
            class FakeAction:
                def __init__(self, text):
                    self.text = text

                def setEnabled(self, _enabled):
                    pass

            class FakeMenu:
                def __init__(self, _parent):
                    self.actions = []

                def addAction(self, text):
                    action = FakeAction(text)
                    self.actions.append(action)
                    return action

                def exec(self, _position):
                    return next(
                        action for action in self.actions
                        if action.text == "Öffnen"
                    )

            with (
                patch.object(page.file_list, "itemAt", return_value=file_item),
                patch("app.gui.pages.folder_page.QMenu", FakeMenu),
            ):
                page._open_tree_context_menu(QPoint(1, 1))
            page._open_context_item("folder", str(root))

            self.assertEqual(opened_files, [str(document)])
            self.assertEqual(opened_paths, [str(root)])
            with patch.object(page.file_viewer, "open_file") as open_file:
                page._open_selected_file(file_item)
            open_file.assert_called_once_with(document)
            page.cleanup()

    def test_customer_and_folder_back_buttons_share_card_alignment(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer_page = CustomerPage(repository)
            folder_page = FolderPage()
            customer_card = customer_page.splitter.widget(0)
            folder_card = folder_page.splitter.widget(0)

            customer_margins = customer_card.layout().contentsMargins()
            folder_margins = folder_card.layout().contentsMargins()
            self.assertEqual(customer_margins.left(), folder_margins.left())
            self.assertEqual(customer_margins.top(), folder_margins.top())
            self.assertEqual(customer_margins.right(), folder_margins.right())
            self.assertEqual(customer_page.back_button.minimumWidth(), 48)
            self.assertEqual(folder_page.back_button.minimumWidth(), 48)

            folder_page.cleanup()
            repository.close()

    def test_folder_page_renders_nested_and_empty_subfolders_as_tree(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "Bilder" / "Innen"
            empty = root / "Leerer Ordner"
            nested.mkdir(parents=True)
            empty.mkdir()
            photo = nested / "foto.jpg"
            photo.write_bytes(b"image")

            page = FolderPage()
            page.set_folder({
                "folder_path": str(root),
                "folder_name": "Projekt",
                "file_count": 1,
                "total_size": photo.stat().st_size,
                "files": [{
                    "filename": photo.name,
                    "path": str(photo),
                    "file_type": "jpg",
                    "file_size": photo.stat().st_size,
                    "relative_dir": "Bilder/Innen",
                }],
                "subfolders": [
                    {
                        "path": str(root / "Bilder"),
                        "name": "Bilder",
                        "children": [{
                            "path": str(nested),
                            "name": "Innen",
                            "children": [],
                        }],
                    },
                    {
                        "path": str(empty),
                        "name": "Leerer Ordner",
                        "children": [],
                    },
                ],
            })

            self.assertEqual(page.file_list.topLevelItemCount(), 2)
            pictures = page.file_list.topLevelItem(0)
            self.assertEqual(pictures.text(0), "Bilder")
            self.assertEqual(pictures.child(0).text(0), "Innen")
            self.assertEqual(pictures.child(0).child(0).text(0), "foto.jpg")
            self.assertEqual(page.file_list.topLevelItem(1).text(0), "Leerer Ordner")
            page.cleanup()

    def test_folder_page_filter_actions_formatting_and_empty_guards(self):
        with patch("app.gui.pages.folder_page.QSettings.value", return_value=[]):
            page = FolderPage()
        page.set_folder({
            "folder_path": "/project", "files": [
                {"filename": "one.pdf", "path": "/project/one.pdf",
                 "file_size": 2 * 1024 * 1024, "modified_date": "invalid"},
                {"filename": "two.txt", "path": "", "file_size": 2048,
                 "modified_date": "2026-01-02"},
                {"filename": "three", "path": "/project/three", "file_size": 3},
            ],
            "subfolders": [{"name": "Virtual", "path": "", "children": []}],
        })
        self.assertEqual(FolderPage._format_size(2 * 1024 * 1024), "2.0 MB")
        self.assertEqual(FolderPage._format_size(2048), "2.0 KB")
        self.assertEqual(FolderPage._format_size(3), "3 B")
        self.assertEqual(FolderPage._path_key(""), "")

        page.file_filter.setText("missing")
        page.file_type_filter.setCurrentIndex(page.file_type_filter.findData("pdf"))
        page._apply_file_filter()
        page.file_filter.clear()
        page._apply_file_filter()
        page._open_selected_file(page.file_list.topLevelItem(0))
        with patch.object(page.file_list, "itemAt", return_value=None):
            page._open_tree_context_menu(QPoint(0, 0))

        class FakeAction:
            def __init__(self, text):
                self.text = text

            def setEnabled(self, _enabled):
                pass

        class FakeMenu:
            selected_text = "Ordner im System öffnen"

            def __init__(self, _parent):
                self.actions = []

            def addAction(self, text):
                action = FakeAction(text)
                self.actions.append(action)
                return action

            def exec(self, _position):
                return next(action for action in self.actions if action.text == self.selected_text)

        opened_paths = []
        managed = []
        page.openPathRequested.connect(opened_paths.append)
        page.manageCustomerRequested.connect(lambda *values: managed.append(values))
        page._open_context_item("file", "")
        page._open_folder()
        page._manage_customer()
        self.assertEqual(opened_paths, ["/project"])
        self.assertEqual(managed[0][0], "/project")
        page.folder_path = ""
        page._open_folder()
        page._manage_customer()

        item = next(
            page.file_list.topLevelItem(index)
            for index in range(page.file_list.topLevelItemCount())
            if page.file_list.topLevelItem(index).data(0, Qt.UserRole)
        )
        with patch.object(page.file_list, "itemAt", return_value=item), \
                patch("app.gui.pages.folder_page.QMenu", FakeMenu):
            page._open_tree_context_menu(QPoint(0, 0))
            FakeMenu.selected_text = "Pfad kopieren"
            page._open_tree_context_menu(QPoint(0, 0))
            empty_item = next(
                page.file_list.topLevelItem(index)
                for index in range(page.file_list.topLevelItemCount())
                if not page.file_list.topLevelItem(index).data(0, Qt.UserRole)
            )
            with patch.object(page.file_list, "itemAt", return_value=empty_item):
                page._open_tree_context_menu(QPoint(0, 0))

        with patch.object(FolderPage, "width", return_value=800):
            page.resizeEvent(QResizeEvent(QSize(800, 500), QSize(1000, 500)))
        self.assertEqual(page.splitter.orientation(), Qt.Vertical)
        with patch.object(FolderPage, "width", return_value=1000):
            page.resizeEvent(QResizeEvent(QSize(1000, 500), QSize(800, 500)))
        self.assertEqual(page.splitter.orientation(), Qt.Horizontal)
        page._save_splitter_sizes()
        page.splitter.setOrientation(Qt.Vertical)
        page._save_splitter_sizes()
        with patch.object(FolderPage, "width", return_value=800):
            page.resizeEvent(QResizeEvent(QSize(800, 500), QSize(800, 500)))
        page.cleanup()

    def test_settings_exposes_explicit_customer_recognition_page(self):
        popup = SettingsPopup(
            "dark",
            "#2db89d",
            recognition_options=CustomerRecognitionOptions(
                enabled=True,
                email_blacklist="intern@example.de",
            ),
            pending_recognition_cases=2,
        )

        self.assertEqual(popup.nav_list.count(), 6)
        self.assertEqual(
            [popup.nav_list.item(index).text() for index in range(popup.nav_list.count())],
            [
                "Allgemein",
                "Indexierung",
                "Suche",
                "Kundenerkennung",
                "Statistik",
                "Aussehen",
            ],
        )
        self.assertIn("Diagnose", popup.diagnostics_text.toPlainText())
        self.assertTrue(popup.clear_customer_data_button.isEnabled())
        self.assertTrue(popup.recognition_enabled.isChecked())
        self.assertEqual(
            popup.recognition_blacklist_fields["email_blacklist"].toPlainText(),
            "intern@example.de",
        )
        self.assertTrue(popup.review_recognition_button.isEnabled())
        self.assertIn("2", popup.review_recognition_button.text())
        self.assertEqual(popup.contrast_slider.value(), 100)
        self.assertEqual(popup.contrast_value_label.text(), "100 %")
        self.assertEqual(popup.font_size_slider.value(), 13)
        self.assertEqual(popup.font_size_value_label.text(), "13 px")
        self.assertEqual(
            popup.stack.widget(3).findChild(QScrollArea).horizontalScrollBarPolicy(),
            Qt.ScrollBarAlwaysOff,
        )
        popup.close()

    def test_statistics_page_formats_last_index_run_date(self):
        popup = SettingsPopup("light", "#2db89d")

        popup.set_statistics(ApplicationStatistics(
            last_indexed_at="2026-08-03T14:05:30",
        ))

        self.assertEqual(
            popup.statistics_widget._value_labels["last_indexed_at"].text(),
            "03.08.2026 14:05",
        )
        popup.close()

    def test_appearance_sliders_update_visible_values(self):
        popup = SettingsPopup("light", "#2db89d")

        popup.contrast_slider.setValue(125)
        popup.font_size_slider.setValue(18)

        self.assertEqual(popup.contrast_value_label.text(), "125 %")
        self.assertEqual(popup.font_size_value_label.text(), "18 px")
        popup.close()

    def test_custom_accent_updates_and_cancel_keeps_previous_color(self):
        popup = SettingsPopup("light", "#2db89d")
        popup.accent_combo.setCurrentIndex(popup.accent_combo.count() - 1)

        with patch(
            "app.gui.settings_popup.QColorDialog.getColor",
            return_value=QColor("#663399"),
        ):
            popup.pick_custom_color()
        self.assertEqual(popup.selected_accent, "#663399")
        self.assertFalse(popup._color_dialog_active)

        with patch(
            "app.gui.settings_popup.QColorDialog.getColor",
            return_value=QColor(),
        ):
            popup.pick_custom_color()
        self.assertEqual(popup.selected_accent, "#663399")
        self.assertFalse(popup._color_dialog_active)
        popup.close()

    def test_index_spinbox_requires_click_before_wheel_adjustment(self):
        popup = SettingsPopup("light", "#2db89d")
        popup.show()
        self.app.processEvents()
        spinbox = popup.max_file_size_spin
        initial_value = spinbox.value()

        def wheel_event():
            return QWheelEvent(
                QPointF(4, 4),
                QPointF(4, 4),
                QPoint(),
                QPoint(0, 120),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.ScrollPhase.ScrollUpdate,
                False,
            )

        event = wheel_event()
        spinbox.wheelEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(spinbox.value(), initial_value)

        QTest.mouseClick(spinbox.lineEdit(), Qt.LeftButton)
        self.assertTrue(spinbox._wheel_adjustment_enabled)
        spinbox.wheelEvent(wheel_event())
        self.assertEqual(spinbox.value(), initial_value + 1)
        popup.close()

    def test_customer_page_enables_hover_tracking_for_contact_rows(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(display_name="Muster", company="Muster"))
            page = CustomerPage(repository)
            page.set_customer(customer)

            self.assertTrue(page.contacts_table.hasMouseTracking())

            page.close()
            repository.close()

    def test_customer_page_only_shows_role_for_companies_with_role(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            page = CustomerPage(repository)
            company = Customer(
                display_name="Muster GmbH",
                company="Muster GmbH",
                entity_type="Unternehmen",
                contacts=[Contact(name="Test", phone="123", email="a@example.de")],
            )

            page.set_customer(company)
            self.assertTrue(page.contacts_table.isColumnHidden(1))
            header = page.contacts_table.horizontalHeader()
            self.assertEqual(header.sectionResizeMode(2), QHeaderView.ResizeToContents)
            self.assertEqual(header.sectionResizeMode(3), QHeaderView.Stretch)
            company.contacts[0].role = "Leitung"
            page.set_customer(company)
            self.assertFalse(page.contacts_table.isColumnHidden(1))
            self.assertEqual(page.contacts_table.item(0, 2).text(), "123")
            self.assertEqual(page.contacts_table.item(0, 3).text(), "a@example.de")
            company.entity_type = "Organisation"
            page.set_customer(company)
            self.assertTrue(page.contacts_table.isColumnHidden(1))

            page.close()
            repository.close()

    def test_customer_page_counts_pending_fields_in_review_button(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(display_name="Muster"))
            repository.apply_project_suggestion(
                int(customer.id), None, "email", "mail@example.de",
                confidence=0.88,
            )
            repository.apply_project_suggestion(
                int(customer.id), None, "phone", "+49 30 123456",
                confidence=0.84,
            )
            page = CustomerPage(repository)
            page.set_customer(customer)

            self.assertEqual(page.review_button.count(), 2)
            self.assertTrue(page.review_button.badge.isVisibleTo(page))
            self.assertEqual(page.edit_button.text(), "Kundendaten bearbeiten")
            self.assertEqual(page.review_button.text(), "Kundendaten prüfen")

            page.close()
            repository.close()

    def test_customer_page_supports_notes_and_journal_tabs(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(
                display_name="Muster",
                company="Muster",
                notes=["Bestehende Notiz"],
            ))
            page = CustomerPage(repository)
            page.set_customer(customer)

            self.assertEqual(page.customer_tabs.tabText(0), "Notizen")
            self.assertEqual(page.customer_tabs.tabText(1), "Journal")
            self.assertEqual(page.customer_tabs.objectName(), "InsetContentTabs")
            self.assertFalse(page.notes.isReadOnly())
            self.assertEqual(page.notes.height(), 196)
            self.assertFalse(page.journal_entries_section.isVisible())

            page.notes.setPlainText("Aktualisierte Notiz")
            self.assertEqual(
                repository.get(int(customer.id)).notes,
                ["Aktualisierte Notiz"],
            )

            page.customer_tabs.setCurrentIndex(1)
            page.show()
            self.app.processEvents()
            journal_layout = page.customer_tabs.currentWidget().layout()
            self.assertEqual(journal_layout.spacing(), 8)
            self.assertTrue(page.journal_entries_section.isVisible())
            page.journal_input.setPlainText("Telefonnotiz am Empfang hinterlegt")
            page.journal_title_input.setText("Telefon")
            page._add_journal_entry()
            page.journal_input.setPlainText("Rückruf erfolgreich")
            page.journal_title_input.setText("Follow-up")
            page._add_journal_entry()
            entries = repository.list_journal_entries(int(customer.id))
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0].body, "Telefonnotiz am Empfang hinterlegt")
            self.assertEqual(entries[1].body, "Rückruf erfolgreich")
            self.assertEqual(len(page._journal_cards), 2)
            self.assertEqual(page._journal_cards[0].entry.title, "Follow-up")
            self.assertEqual(
                page._journal_cards[0].entry.body,
                "Rückruf erfolgreich",
            )
            self.assertEqual(page._journal_cards[0].styleSheet(), "")

            page.close()
            repository.close()

    def test_customer_editor_excludes_pre_2016_folder_from_selection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_folder = root / "Blower Door" / "2015" / "Legacy Kunde"
            legacy_folder.mkdir(parents=True)
            repository = CustomerRepository(root / "customers.db")

            dialog = CustomerEditorDialog(
                repository,
                folder_path=str(legacy_folder),
                suggested_name="Legacy Kunde",
            )

            self.assertEqual(dialog.selected_folders_table.rowCount(), 0)
            self.assertEqual(dialog._selected_folder_values(), [])

            dialog.close()
            repository.close()


if __name__ == "__main__":
    unittest.main()
