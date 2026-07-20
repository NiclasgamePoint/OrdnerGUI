from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.customer_models import Contact, Customer
from app.core.customer_recognition_models import RecognitionCandidate, RecognitionStats
from app.core.customer_repository import CustomerRepository
from app.core.config import IndexOptions
from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters
from app.gui.workers.search_worker import SearchWorker


class CustomerRepositoryTests(unittest.TestCase):
    def test_fuzzy_multiword_search_ranks_matches_across_project_fields(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            target = repository.save(Customer(
                display_name="Wahnhorst",
                company="Wahnhorst Energieberatung",
            ))
            repository.add_folder_to_customer(
                int(target.id),
                str(Path(directory) / "Blower Door" / "2026" / "Wahnhorst, Kaltenkirchen"),
                "Blower Door",
            )
            distractor = repository.save(Customer(
                display_name="Horst Beispiel",
                company="Horst Beispiel",
                city="Hamburg",
            ))

            results = repository.search("Horst Kaltenkirchen")
            typo_results = repository.search("wahnhorst kaltenkirchn")
            tolerant_results = repository.search("Horst völligfalsch")

            self.assertEqual(results[0].id, target.id)
            self.assertEqual(typo_results[0].id, target.id)
            self.assertIn(target.id, [customer.id for customer in tolerant_results])
            self.assertNotEqual(results[0].id, distractor.id)
            repository.close()

    def test_add_folder_to_existing_customer_and_reject_duplicate_assignment(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "customers.db"
            repository = CustomerRepository(database)
            first = repository.save(Customer(
                display_name="Muster GmbH",
                company="Muster GmbH",
            ))
            second = repository.save(Customer(
                display_name="Andere GmbH",
                company="Andere GmbH",
            ))
            folder = Path(directory) / "Blower Door" / "2026" / "Projekt A"

            updated = repository.add_folder_to_customer(
                first.id,
                str(folder),
                "Blower Door",
            )

            self.assertEqual(repository.get_by_folder(str(folder)).id, first.id)
            self.assertIn(str(folder.resolve()), updated.folder_paths)
            self.assertIn("Blower Door", updated.service_types)
            self.assertFalse(any(
                path.startswith("customer://") for path in updated.folder_paths
            ))
            with self.assertRaises(ValueError):
                repository.add_folder_to_customer(second.id, str(folder))
            repository.close()

    def test_project_records_are_created_from_customer_folders(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "customers.db"
            repository = CustomerRepository(database)
            folder_a = Path(directory) / "Blower Door" / "2020" / "Mustermann, Musterstadt"
            folder_b = Path(directory) / "Blower Door" / "2022" / "Mustermann, Hamburg"
            customer = repository.save(Customer(
                display_name="Mustermann",
                company="Mustermann",
                folder_paths=[str(folder_a), str(folder_b)],
                service_types=["Blower Door"],
            ))

            projects = repository.list_projects_for_customer(int(customer.id))
            services = repository.connection.execute(
                "SELECT name FROM service_types ORDER BY name"
            ).fetchall()

            self.assertEqual(len(projects), 2)
            self.assertEqual({project.service_type for project in projects}, {"Blower Door"})
            self.assertEqual([row[0] for row in services], ["Blower Door"])
            self.assertEqual(repository.find_project_by_folder(str(folder_a)).customer_id, customer.id)
            self.assertEqual(set(repository.get(customer.id).folder_paths), {
                str(folder_a.resolve()),
                str(folder_b.resolve()),
            })
            repository.close()

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
            self.assertEqual(repository.search("prioritat")[0].id, updated.id)
            self.assertEqual(repository.search("Erika")[0].id, updated.id)
            self.assertEqual(repository.search("vollkommenunbekannt"), [])

            repository.delete(updated.id)
            self.assertIsNone(repository.get(updated.id))
            repository.close()

    def test_clear_all_customer_data_keeps_schema_and_removes_customer_owned_data(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "customers.db"
            repository = CustomerRepository(database)
            customer = repository.save(Customer(
                display_name="Muster GmbH",
                company="Muster GmbH",
                contacts=[Contact("Erika Muster", "", "e@example.de", "12345")],
                notes=["Rueckruf vereinbart"],
                tags=["VIP"],
            ))
            folder = Path(directory) / "Blower Door" / "2026" / "Muster GmbH, Kiel"
            repository.add_folder_to_customer(
                int(customer.id),
                str(folder),
                "Blower Door",
            )
            project = repository.find_project_by_folder(str(folder))
            self.assertIsNotNone(project)
            repository.apply_project_suggestion(
                int(customer.id),
                int(project.id),
                "email",
                "neu@example.de",
                str(folder / "angebot.pdf"),
            )
            repository.replace_pending_recognition_cases([
                RecognitionCandidate(
                    recognition_key="muster",
                    display_name="Muster GmbH",
                    city="Kiel",
                    folder_paths=[str(folder)],
                    service_types=["Blower Door"],
                    years=[2026],
                    reason="Testfall",
                )
            ])
            repository.record_recognition_run(RecognitionStats(detected=1, pending=1))

            repository.clear_all_customer_data()

            self.assertEqual(repository.list_customers(), [])
            self.assertEqual(repository.pending_recognition_count(), 0)
            self.assertEqual(repository.last_recognition_run(), {})
            self.assertEqual(
                repository.connection.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
                0,
            )
            self.assertEqual(
                repository.connection.execute("SELECT COUNT(*) FROM customer_projects").fetchone()[0],
                0,
            )
            self.assertEqual(
                repository.connection.execute("SELECT COUNT(*) FROM service_types").fetchone()[0],
                0,
            )
            self.assertEqual(
                repository.connection.execute("SELECT COUNT(*) FROM customer_data_suggestions").fetchone()[0],
                0,
            )
            self.assertEqual(
                repository.connection.execute("SELECT COUNT(*) FROM customer_types").fetchone()[0],
                3,
            )
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
