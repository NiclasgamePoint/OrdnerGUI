from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.config import CustomerRecognitionOptions, IndexOptions
from app.core.customer_models import Contact, Customer
from app.core.customer_repository import CustomerRepository
from app.core.index_manager import IndexManager
from app.services.customer_recognition import (
    CustomerRecognitionService,
    RecognitionBlacklist,
)
from app.services.customer_suggestion import CustomerSuggestion


class CustomerRecognitionTests(unittest.TestCase):
    def _build_index(self, root: Path, index_path: Path):
        manager = IndexManager(index_path, options=IndexOptions(ocr_enabled=False))
        manager.synchronize_directory(root, full_rebuild=True)
        manager.close()

    def test_unique_candidate_is_created_idempotently(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            project = root / "Energieberatung" / "2026" / "Müller, Berlin"
            project.mkdir(parents=True)
            (project / "kunde.txt").write_text(
                "Kunde: Max Müller\nmax@example.de\n030 12345678",
                encoding="utf-8",
            )
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)

            service = CustomerRecognitionService(
                index_path,
                customer_path,
                CustomerRecognitionOptions(enabled=True),
            )
            first = service.synchronize()
            second = service.synchronize()
            repository = CustomerRepository(customer_path)
            customers = repository.list_customers()

            self.assertEqual(first.created, 1)
            self.assertEqual(second.created, 0)
            self.assertEqual(len(customers), 1)
            self.assertEqual(customers[0].display_name, "Müller")
            self.assertEqual(customers[0].entity_type, "Privatperson")
            self.assertEqual(customers[0].city, "Berlin")
            self.assertIn("Energieberatung", customers[0].service_types)
            self.assertEqual(
                repository.get_by_folder(str(project / "Unterordner")).id,
                customers[0].id,
            )
            repository.close()

    def test_blacklist_filters_all_supported_extracted_value_types(self):
        options = CustomerRecognitionOptions(
            enabled=True,
            email_blacklist="ICH@EXAMPLE.DE",
            phone_blacklist="040 / 12 34 56",
            name_blacklist="Eigener Name",
            address_blacklist="Eigenweg 7 12345 Hamburg",
            text_blacklist="internes kennwort",
        )
        suggestion = CustomerSuggestion(
            email="ich@example.de",
            phone="040-123456",
            street="Eigenweg 7",
            postal_code="12345",
            city="Hamburg",
            contacts=[
                Contact("Eigener Name", email="andere@example.de"),
                Contact("Kunde", email="internes.kennwort@example.de"),
                Contact("Kunde Zwei", email="kunde@example.de", phone="030 987654"),
            ],
        )

        filtered = RecognitionBlacklist(options).filter_suggestion(suggestion)

        self.assertEqual(filtered.email, "")
        self.assertEqual(filtered.phone, "")
        self.assertEqual(filtered.street, "")
        self.assertEqual(filtered.postal_code, "")
        self.assertEqual(len(filtered.contacts), 1)
        self.assertEqual(filtered.contacts[0].name, "Kunde Zwei")

    def test_manual_fields_are_not_overwritten(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            project = root / "DEKRA" / "2026" / "Müller, Berlin"
            project.mkdir(parents=True)
            (project / "info.txt").write_text(
                "neu@example.de\n030 999999\nNeuweg 8\n12345 Berlin",
                encoding="utf-8",
            )
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)
            repository = CustomerRepository(customer_path)
            existing = repository.save(Customer(
                display_name="Müller",
                company="Manuell gepflegt",
                city="Berlin",
                email="manuell@example.de",
                phone="030 111111",
                street="Altweg 1",
            ))
            repository.close()

            stats = CustomerRecognitionService(
                index_path,
                customer_path,
                CustomerRecognitionOptions(enabled=True),
            ).synchronize()
            repository = CustomerRepository(customer_path)
            updated = repository.get(existing.id)

            self.assertEqual(stats.assigned, 1)
            self.assertEqual(updated.company, "Manuell gepflegt")
            self.assertEqual(updated.email, "manuell@example.de")
            self.assertEqual(updated.phone, "030 111111")
            self.assertEqual(updated.street, "Altweg 1")
            self.assertIn(str(project.resolve()), updated.folder_paths)
            suggestions = repository.list_data_suggestions(updated.id)
            self.assertTrue(any(
                suggestion.field_name == "email"
                and suggestion.suggested_value == "neu@example.de"
                for suggestion in suggestions
            ))
            repository.close()

    def test_recognition_creates_project_records_and_service_types(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            projects = [
                root / "Blower Door" / "2020" / "Mustermann, Musterstadt",
                root / "Blower Door" / "2022" / "Mustermann, Hamburg",
                root / "Baubegleitung" / "2024" / "Mustermann, Musterstadt",
            ]
            for project in projects:
                project.mkdir(parents=True)
                (project / "info.txt").write_text("Kontakt: Max Mustermann", encoding="utf-8")
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)

            stats = CustomerRecognitionService(
                index_path,
                customer_path,
                CustomerRecognitionOptions(enabled=True),
            ).synchronize()
            repository = CustomerRepository(customer_path)
            customers = repository.list_customers()
            customer_projects = repository.list_projects_for_customer(int(customers[0].id))

            self.assertEqual(stats.created, 1)
            self.assertEqual(len(customers), 1)
            self.assertEqual(len(customer_projects), 3)
            self.assertEqual(
                {project.service_type for project in customer_projects},
                {"Blower Door", "Baubegleitung"},
            )
            self.assertEqual(
                repository.connection.execute("SELECT COUNT(*) FROM service_types").fetchone()[0],
                2,
            )
            repository.close()

    def test_pre_2016_structured_folder_waits_for_review(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            legacy = root / "Blower Door" / "2015" / "Mustermann, Musterstadt"
            legacy.mkdir(parents=True)
            (legacy / "info.txt").write_text("Altprojekt", encoding="utf-8")
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)

            stats = CustomerRecognitionService(
                index_path,
                customer_path,
                CustomerRecognitionOptions(enabled=True),
            ).synchronize()
            repository = CustomerRepository(customer_path)
            cases = repository.list_pending_recognition_cases()

            self.assertEqual(stats.created, 0)
            self.assertEqual(stats.pending, 1)
            self.assertEqual(len(repository.list_customers()), 0)
            self.assertEqual(cases[0].display_name, "Mustermann")
            self.assertIn("vor 2016", cases[0].reason)
            repository.close()

    def test_similar_name_waits_for_persistent_review_decision(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            project = root / "DEKRA" / "2026" / "Müller GmbH, Berlin"
            project.mkdir(parents=True)
            (project / "a.txt").write_text("A", encoding="utf-8")
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)
            repository = CustomerRepository(customer_path)
            existing = repository.save(Customer(
                display_name="Müller Gmb",
                company="Manuell gepflegt",
                city="Berlin",
            ))
            repository.close()
            options = CustomerRecognitionOptions(enabled=True)
            service = CustomerRecognitionService(index_path, customer_path, options)

            initial = service.synchronize()
            repository = CustomerRepository(customer_path)
            cases = repository.list_pending_recognition_cases()
            self.assertEqual(initial.pending, 1)
            self.assertEqual(len(repository.list_customers()), 1)
            repository.close()

            affected = service.resolve_case(cases[0], "assign", int(existing.id))
            repeated = service.synchronize()
            repository = CustomerRepository(customer_path)
            customer = repository.get(affected[0])

            self.assertEqual(repeated.pending, 0)
            self.assertEqual(repository.pending_recognition_count(), 0)
            self.assertEqual(len(repository.list_customers()), 1)
            self.assertIn(str(project.resolve()), customer.folder_paths)
            repository.close()

            (project / "a.txt").write_text("neu@example.de", encoding="utf-8")
            self._build_index(root, index_path)
            changed = service.synchronize()
            self.assertEqual(changed.pending, 1)

    def test_project_roots_with_same_name_are_automatically_combined(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            projects = [
                root / "Blower Door" / "2026" / "AB S+E, Ersatzbau Arche",
                root / "Blower Door" / "2025" / "AB S+E, Erweiterung Amt",
                root / "DEKRA" / "2024" / "AB S+E, Kita Rickling",
            ]
            for position, project in enumerate(projects):
                project.mkdir(parents=True)
                (project / f"{position}.txt").write_text("A", encoding="utf-8")
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)

            stats = CustomerRecognitionService(
                index_path,
                customer_path,
                CustomerRecognitionOptions(enabled=True),
            ).synchronize()
            repository = CustomerRepository(customer_path)
            customers = repository.list_customers()

            self.assertEqual(stats.created, 1)
            self.assertEqual(stats.pending, 0)
            self.assertEqual(len(customers), 1)
            self.assertEqual(customers[0].display_name, "AB S+E")
            self.assertEqual(set(customers[0].folder_paths), {
                str(project.resolve()) for project in projects
            })
            self.assertEqual(set(customers[0].service_types), {"Blower Door", "DEKRA"})
            repository.close()

    def test_existing_duplicates_are_merged_without_losing_related_data(self):
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "customers.db"
            repository = CustomerRepository(database_path)
            first = repository.save(Customer(
                display_name="AB S+E",
                company="AB S+E",
                email="kontakt@example.de",
                city="Erster Ort",
                folder_paths=[str(Path(directory) / "Projekt A")],
                service_types=["Blower Door"],
                contacts=[Contact("Erster Kontakt", email="eins@example.de")],
                notes=["Erste Notiz"],
                tags=["Bestand"],
            ))
            second_folder = str((Path(directory) / "Projekt B").resolve())
            cursor = repository.connection.execute(
                """
                INSERT INTO customers
                    (folder_path, display_name, company, phone, street, city)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    second_folder,
                    "  ab s+e  ",
                    "AB S+E",
                    "040 12345",
                    "Zweiter Weg 2",
                    "Zweiter Ort",
                ),
            )
            duplicate_id = int(cursor.lastrowid)
            repository.connection.execute(
                "INSERT INTO customer_folders (customer_id, folder_path) VALUES (?, ?)",
                (duplicate_id, second_folder),
            )
            repository.connection.execute(
                "INSERT INTO customer_services (customer_id, name) VALUES (?, ?)",
                (duplicate_id, "DEKRA"),
            )
            repository.connection.execute(
                "INSERT INTO contacts (customer_id, name, phone) VALUES (?, ?, ?)",
                (duplicate_id, "Zweiter Kontakt", "040 98765"),
            )
            repository.connection.execute(
                "INSERT INTO notes (customer_id, body) VALUES (?, ?)",
                (duplicate_id, "Zweite Notiz"),
            )
            repository.connection.execute("INSERT OR IGNORE INTO tags (name) VALUES ('Import')")
            tag_id = repository.connection.execute(
                "SELECT id FROM tags WHERE name='Import'"
            ).fetchone()[0]
            repository.connection.execute(
                "INSERT INTO customer_tags (customer_id, tag_id) VALUES (?, ?)",
                (duplicate_id, tag_id),
            )
            repository.connection.commit()

            merged_ids = repository.merge_duplicate_customers_by_name()
            customers = repository.list_customers()

            self.assertEqual(len(customers), 1)
            merged = customers[0]
            absorbed_id = duplicate_id if merged.id != duplicate_id else int(first.id)
            self.assertEqual(merged_ids[absorbed_id], int(merged.id))
            self.assertEqual(set(merged.folder_paths), {
                str((Path(directory) / "Projekt A").resolve()), second_folder
            })
            self.assertEqual(set(merged.service_types), {"Blower Door", "DEKRA"})
            self.assertEqual({item.name for item in merged.contacts}, {
                "Erster Kontakt", "Zweiter Kontakt"
            })
            self.assertEqual(set(merged.notes), {"Erste Notiz", "Zweite Notiz"})
            self.assertEqual(set(merged.tags), {"Bestand", "Import"})
            self.assertEqual(merged.email, "kontakt@example.de")
            self.assertEqual(merged.phone, "040 12345")
            self.assertEqual(
                repository.connection.execute(
                    "SELECT COUNT(*) FROM customer_merge_log WHERE absorbed_id=?",
                    (absorbed_id,),
                ).fetchone()[0],
                1,
            )
            repository.close()

    def test_saving_same_name_reuses_existing_customer(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            first = repository.save(Customer(
                display_name="Müller",
                folder_paths=[str(Path(directory) / "A")],
                service_types=["DEKRA"],
            ))
            repeated = repository.save(Customer(
                display_name=" müller ",
                folder_paths=[str(Path(directory) / "B")],
                service_types=["Blower Door"],
            ))

            self.assertEqual(repeated.id, first.id)
            self.assertEqual(len(repository.list_customers()), 1)
            self.assertEqual(set(repeated.service_types), {"DEKRA", "Blower Door"})
            self.assertEqual(len(repeated.folder_paths), 2)
            repository.close()


if __name__ == "__main__":
    unittest.main()
