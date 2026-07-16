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
            repository.close()

    def test_ambiguous_roots_wait_for_persistent_review_decision(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            first = root / "DEKRA" / "2026" / "Müller, Berlin"
            second = root / "Baubegleitung" / "2025" / "Müller, Berlin"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "a.txt").write_text("A", encoding="utf-8")
            (second / "b.txt").write_text("B", encoding="utf-8")
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)
            options = CustomerRecognitionOptions(enabled=True)
            service = CustomerRecognitionService(index_path, customer_path, options)

            initial = service.synchronize()
            repository = CustomerRepository(customer_path)
            cases = repository.list_pending_recognition_cases()
            self.assertEqual(initial.pending, 1)
            self.assertEqual(len(repository.list_customers()), 0)
            repository.close()

            affected = service.resolve_case(cases[0], "together")
            repeated = service.synchronize()
            repository = CustomerRepository(customer_path)
            customer = repository.get(affected[0])

            self.assertEqual(repeated.pending, 0)
            self.assertEqual(repository.pending_recognition_count(), 0)
            self.assertEqual(len(repository.list_customers()), 1)
            self.assertEqual(set(customer.folder_paths), {
                str(first.resolve()), str(second.resolve())
            })
            repository.close()

            (first / "a.txt").write_text("neu@example.de", encoding="utf-8")
            self._build_index(root, index_path)
            changed = service.synchronize()
            self.assertEqual(changed.pending, 1)

    def test_multiple_existing_matches_are_never_merged(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Bauvorhaben"
            project = root / "DEKRA" / "2026" / "Müller, Berlin"
            project.mkdir(parents=True)
            (project / "a.txt").write_text("A", encoding="utf-8")
            index_path = base / "index.db"
            customer_path = base / "customers.db"
            self._build_index(root, index_path)
            repository = CustomerRepository(customer_path)
            repository.save(Customer(display_name="Müller", company="A", city="Berlin"))
            repository.save(Customer(display_name="Müller", company="B", city="Berlin"))
            repository.close()

            stats = CustomerRecognitionService(
                index_path,
                customer_path,
                CustomerRecognitionOptions(enabled=True),
            ).synchronize()
            repository = CustomerRepository(customer_path)

            self.assertEqual(stats.pending, 1)
            self.assertEqual(len(repository.list_customers()), 2)
            repository.close()


if __name__ == "__main__":
    unittest.main()
