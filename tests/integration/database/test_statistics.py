from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from app.core.config import IndexOptions
from app.core.customer_models import Contact, Customer
from app.core.customer_repository import CustomerRepository
from app.core.index_manager import IndexManager
from app.core.statistics import StatisticsService


class StatisticsServiceTests(unittest.TestCase):
    def test_loads_customer_project_and_index_metrics(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "Bauvorhaben"
            project = source / "DEKRA" / "2026" / "Muster GmbH, Berlin"
            project.mkdir(parents=True)
            document = project / "bericht.txt"
            document.write_text("Auswertbarer Dokumentinhalt", encoding="utf-8")
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            with IndexManager(
                index_path,
                options=IndexOptions(ocr_enabled=False),
            ) as manager:
                manager.synchronize_directory(source, full_rebuild=True)
            repository = CustomerRepository(customer_path)
            repository.save(Customer(
                display_name="Muster GmbH",
                folder_path=str(project),
                service_types=["DEKRA"],
                contacts=[Contact("Max Muster", email="max@example.de")],
            ))
            repository.close()

            statistics = StatisticsService().load(index_path, customer_path)

            self.assertEqual(statistics.customer_count, 1)
            self.assertEqual(statistics.contact_count, 1)
            self.assertEqual(statistics.project_count, 1)
            self.assertEqual(statistics.service_count, 1)
            self.assertEqual(statistics.file_count, 1)
            self.assertEqual(statistics.total_file_size, document.stat().st_size)
            self.assertEqual(statistics.content_count, 1)
            self.assertTrue(statistics.last_indexed_at)

    def test_missing_databases_return_zero_statistics(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            statistics = StatisticsService().load(
                base / "missing-index.db",
                base / "missing-customers.db",
            )
            self.assertEqual(statistics.customer_count, 0)
            self.assertEqual(statistics.project_count, 0)
            self.assertEqual(statistics.file_count, 0)

    def test_content_state_database_supplies_completed_document_count(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.mkdir()
            index_path = base / "index.db"
            with IndexManager(index_path) as manager:
                manager.synchronize_directory(source, full_rebuild=True)
            state_path = base / "state.db"
            with sqlite3.connect(state_path) as connection:
                connection.execute("CREATE TABLE documents (status TEXT)")
                connection.executemany(
                    "INSERT INTO documents(status) VALUES (?)",
                    [("completed",), ("completed",), ("pending",)],
                )
            statistics = StatisticsService().load(
                index_path,
                base / "missing-customers.db",
                state_path,
            )
            self.assertEqual(statistics.content_count, 2)


if __name__ == "__main__":
    unittest.main()
