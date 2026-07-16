from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.customer_models import Contact, Customer
from app.core.customer_repository import CustomerRepository
from app.core.config import IndexOptions
from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters
from app.gui.workers.search_worker import SearchWorker


class CustomerRepositoryTests(unittest.TestCase):
    def test_customer_contacts_notes_and_tags_crud(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "customers.db"
            repository = CustomerRepository(database)
            customer = repository.save(Customer(
                folder_path="/data/DEKRA/2026/Muster GmbH",
                display_name="Muster GmbH",
                company="Muster GmbH",
                contacts=[Contact("Erika Muster", "Projektleitung", "e@example.de", "123")],
                notes=["Erstkontakt erfolgt"],
                tags=["Gewerbe", "Priorität A", "gewerbe"],
            ))
            self.assertIsNotNone(customer.id)
            self.assertEqual(len(customer.contacts), 1)
            self.assertEqual(len(customer.notes), 1)
            self.assertEqual(len(customer.tags), 2)

            customer.phone = "456"
            customer.notes.append("Angebot versendet")
            updated = repository.save(customer)
            self.assertEqual(updated.phone, "456")
            self.assertEqual(len(updated.notes), 2)
            self.assertEqual(
                repository.get_by_folder("/data/DEKRA/2026/Muster GmbH").display_name,
                "Muster GmbH",
            )
            self.assertEqual(repository.search("Priorität")[0].id, updated.id)
            self.assertEqual(repository.search("Erika")[0].id, updated.id)

            repository.delete(updated.id)
            self.assertIsNone(repository.get(updated.id))
            repository.close()

    def test_customer_tag_is_returned_as_separate_customer_search_result(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            project = root / "DEKRA" / "2026" / "Ordnername"
            project.mkdir(parents=True)
            (project / "datei.txt").write_text("Inhalt", encoding="utf-8")
            index_path = Path(directory) / "index.db"
            manager = IndexManager(index_path, options=IndexOptions(ocr_enabled=False))
            manager.synchronize_directory(root, full_rebuild=True)
            manager.close()
            customer_path = Path(directory) / "customers.db"
            repository = CustomerRepository(customer_path)
            repository.save(Customer(
                folder_path=str(project), display_name="Abweichender Name", tags=["VIP"]
            ))
            repository.close()

            captured = []
            worker = SearchWorker(
                index_path, 1, "customers", "VIP", 100, SearchFilters(), 1, 25,
                customer_path,
            )
            worker.completed.connect(lambda *args: captured.append(args))
            worker.run()
            page = captured[0][2]
            self.assertEqual(page.total, 1)
            self.assertEqual(page.items[0].display_name, "Abweichender Name")


if __name__ == "__main__":
    unittest.main()
