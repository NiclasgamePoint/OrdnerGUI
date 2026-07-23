from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.customer_models import Contact, Customer
from app.core.customer_recognition_models import (
    ExtractionEvidence,
    RecognitionCandidate,
    RecognitionStats,
)
from app.core.customer_repository import CustomerRepository
from app.core.config import CustomerRecognitionOptions, IndexOptions
from app.core.index_manager import IndexManager
from app.core.search_models import SearchFilters
from app.gui.workers.search_worker import SearchWorker


class CustomerRepositoryTests(unittest.TestCase):
    @staticmethod
    def _name_candidate(name: str, automatic: bool = True) -> RecognitionCandidate:
        return RecognitionCandidate(
            recognition_key="muller",
            display_name="Müller",
            city="Berlin",
            folder_paths=[],
            service_types=[],
            years=[],
            contacts=[Contact(name=name)],
            evidence=[ExtractionEvidence(
                field_name="contact_name",
                value=name,
                normalized_value=name.casefold(),
                source_path="/tmp/Müller, Berlin",
                excerpt=name,
                position=0,
                rule="Testregel",
                confidence=0.94 if automatic else 0.80,
                automatic=automatic,
            )],
        )

    def test_pending_surname_contact_is_upgraded_and_accepted_as_card(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(display_name="Müller"))

            repository.apply_contact_scan_candidate(
                int(customer.id), self._name_candidate("Müller")
            )
            repository.apply_contact_scan_candidate(
                int(customer.id), self._name_candidate("Max Müller")
            )

            pending = repository.list_data_suggestions(int(customer.id))
            self.assertEqual(len(pending), 1)
            self.assertTrue(pending[0].is_contact)
            self.assertEqual(pending[0].contact_name, "Max Müller")
            repository.resolve_data_suggestion(int(pending[0].id), True)
            self.assertEqual(
                repository.get(int(customer.id)).contacts,
                [Contact(name="Max Müller")],
            )
            repository.close()

    def test_manual_surname_contact_is_not_renamed_automatically(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(
                display_name="Müller",
                contacts=[Contact(name="Müller")],
            ))

            repository.apply_contact_scan_candidate(
                int(customer.id), self._name_candidate("Max Müller")
            )

            self.assertEqual(
                [contact.name for contact in repository.get(int(customer.id)).contacts],
                ["Müller"],
            )
            pending = repository.list_data_suggestions(int(customer.id))
            self.assertEqual([item.contact_name for item in pending], ["Max Müller"])
            repository.close()

    def test_accepted_name_suggestion_creates_contact(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(display_name="Muster GmbH"))
            suggestion = repository.apply_project_suggestion(
                int(customer.id), None, "contact_name", "Erika Muster",
                confidence=0.80,
            )

            repository.resolve_data_suggestion(int(suggestion.id), True)

            self.assertEqual(
                repository.get(int(customer.id)).contacts,
                [Contact(name="Erika Muster")],
            )
            repository.close()

    def test_weak_contact_name_is_stored_as_field_suggestion(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(display_name="Muster GmbH"))
            candidate = self._name_candidate("Erika Muster", automatic=False)
            candidate.contacts = []

            repository.apply_recognition_candidate(candidate, int(customer.id))

            suggestions = repository.list_data_suggestions(int(customer.id))
            self.assertEqual(len(suggestions), 1)
            self.assertEqual(suggestions[0].field_name, "contact")
            self.assertTrue(suggestions[0].is_contact)
            self.assertEqual(suggestions[0].suggested_value, "Erika Muster")
            repository.close()

    def test_rejected_contact_card_stays_hidden_after_repository_restart(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "customers.db"
            repository = CustomerRepository(database)
            customer = repository.save(Customer(display_name="Muster GmbH"))
            contact = Contact(
                "Maike Mayer", "Architektin", "maike@example.de", "040 12345"
            )
            suggestion = repository.apply_contact_suggestion(
                int(customer.id), None, contact
            )
            repository.resolve_data_suggestion(int(suggestion.id), False)
            repository.close()

            repository = CustomerRepository(database)
            duplicate = repository.apply_contact_suggestion(
                int(customer.id), None, contact, source_path="/tmp/anderes.pdf"
            )
            self.assertIsNone(duplicate)
            self.assertEqual(repository.list_data_suggestions(int(customer.id)), [])
            repository.close()

    def test_accepted_contact_card_fills_blanks_without_overwriting_manual_data(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(
                display_name="Muster GmbH",
                contacts=[Contact("Maike Mayer", email="manuell@example.de")],
            ))
            suggestion = repository.apply_contact_suggestion(
                int(customer.id), None,
                Contact(
                    "Maike Mayer", "Architektin",
                    "erkannt@example.de", "040 12345",
                ),
            )
            repository.resolve_data_suggestion(int(suggestion.id), True)

            self.assertEqual(
                repository.get(int(customer.id)).contacts,
                [Contact(
                    "Maike Mayer", "Architektin",
                    "manuell@example.de", "040 12345",
                )],
            )
            repository.close()

    def test_data_suggestion_keeps_evidence_and_can_be_accepted_or_rejected(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(
                display_name="Muster",
                email="alt@example.de",
            ))
            accepted = repository.apply_project_suggestion(
                int(customer.id), None, "email", "neu@example.de",
                "/tmp/Anschreiben.pdf", "E-Mail: neu@example.de",
                "Beschriftetes Kontaktfeld", 0.89,
            )
            rejected = repository.apply_project_suggestion(
                int(customer.id), None, "phone", "+49 30 123456",
                confidence=0.82,
            )

            pending = repository.list_data_suggestions(int(customer.id))
            self.assertEqual(len(pending), 2)
            self.assertEqual(pending[0].confidence, 0.89)
            self.assertEqual(pending[0].excerpt, "E-Mail: neu@example.de")
            repository.resolve_data_suggestion(int(accepted.id), True)
            repository.resolve_data_suggestion(int(rejected.id), False)

            self.assertEqual(repository.get(int(customer.id)).email, "neu@example.de")
            self.assertEqual(repository.list_data_suggestions(int(customer.id)), [])
            self.assertEqual(len(repository.list_data_suggestions(
                int(customer.id), "accepted"
            )), 1)
            self.assertEqual(len(repository.list_data_suggestions(
                int(customer.id), "rejected"
            )), 1)
            repository.close()

    def test_rescan_keeps_rejected_and_accepted_suggestions_closed(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            customer = repository.save(Customer(display_name="Muster"))
            rejected = repository.apply_project_suggestion(
                int(customer.id), None, "email", "erneut@example.de",
                confidence=0.75,
            )
            accepted = repository.apply_project_suggestion(
                int(customer.id), None, "phone", "+49 30 123456",
                confidence=0.78,
            )
            repository.resolve_data_suggestion(int(rejected.id), False)
            repository.resolve_data_suggestion(int(accepted.id), True)

            rejected_again = repository.apply_project_suggestion(
                int(customer.id), None, "email", "erneut@example.de",
                excerpt="erneuter Fund", confidence=0.80,
                reopen_rejected=True,
            )
            protected = repository.apply_project_suggestion(
                int(customer.id), None, "phone", "+49 30 123456",
                confidence=0.82, reopen_rejected=True,
            )

            self.assertIsNone(rejected_again)
            self.assertIsNone(protected)
            self.assertEqual(len(repository.list_data_suggestions(
                int(customer.id)
            )), 0)
            repository.close()

    def test_blacklist_cleanup_only_removes_automatic_values(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            automatic = repository.apply_recognition_candidate(RecognitionCandidate(
                recognition_key="auto",
                display_name="Automatisch",
                city="Berlin",
                folder_paths=[str(Path(directory) / "Automatisch, Berlin")],
                service_types=["DEKRA"],
                years=[2026],
                email="team@example.de",
                evidence=[ExtractionEvidence(
                    "email", "team@example.de", "team@example.de",
                    "/tmp/Anschreiben.pdf", "E-Mail: team@example.de", 4,
                    "E-Mail im Empfängerblock", 0.95, True,
                )],
            ))
            manual = repository.save(Customer(
                display_name="Manuell",
                email="team@example.de",
            ))

            result = repository.cleanup_automatic_blacklisted_values(
                CustomerRecognitionOptions(email_blacklist="team@example.de")
            )

            self.assertEqual(result["fields"], 1)
            self.assertEqual(repository.get(automatic.id).email, "")
            self.assertEqual(repository.get(manual.id).email, "team@example.de")
            repository.close()

    def test_manual_edit_removes_automatic_provenance(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            automatic = repository.apply_recognition_candidate(RecognitionCandidate(
                recognition_key="auto",
                display_name="Automatisch",
                city="Berlin",
                folder_paths=[str(Path(directory) / "Automatisch, Berlin")],
                service_types=["DEKRA"],
                years=[2026],
                email="auto@example.de",
                evidence=[ExtractionEvidence(
                    "email", "auto@example.de", "auto@example.de",
                    "/tmp/Anschreiben.pdf", "E-Mail: auto@example.de", 4,
                    "E-Mail im Empfängerblock", 0.95, True,
                )],
            ))
            automatic.email = "bestaetigt@example.de"
            repository.save(automatic)

            result = repository.cleanup_automatic_blacklisted_values(
                CustomerRecognitionOptions(email_blacklist="bestaetigt@example.de")
            )

            self.assertEqual(result["fields"], 0)
            self.assertEqual(
                repository.get(automatic.id).email, "bestaetigt@example.de"
            )
            repository.close()

    def test_frequent_values_create_configurable_blacklist_suggestion(self):
        with TemporaryDirectory() as directory:
            repository = CustomerRepository(Path(directory) / "customers.db")
            candidates = [
                RecognitionCandidate(
                    recognition_key=f"kunde-{number}",
                    display_name=f"Kunde {number}",
                    city="Berlin",
                    folder_paths=[str(Path(directory) / f"Kunde {number}, Berlin")],
                    service_types=["DEKRA"],
                    years=[2026],
                    evidence=[ExtractionEvidence(
                        "email", "intern@example.de", "intern@example.de",
                        f"/tmp/{number}.pdf", "intern@example.de", 1,
                        "E-Mail-Fund", 0.72, False,
                    )],
                )
                for number in range(5)
            ]

            repository.record_extraction_observations(candidates, threshold=5)
            suggestions = repository.list_blacklist_suggestions()

            self.assertEqual(len(suggestions), 1)
            self.assertEqual(suggestions[0]["value"], "intern@example.de")
            self.assertEqual(suggestions[0]["folder_count"], 5)
            repository.close()
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
            self.assertEqual(repository.search("Muster")[0].id, updated.id)
            self.assertEqual(repository.search("Priorität"), [])
            self.assertEqual(repository.search("Erika"), [])
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

    def test_customer_name_outside_folder_path_does_not_create_search_result(self):
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
                index_path, 1, "customers", "Abweichender", 100, SearchFilters(), 1, 25,
                customer_path,
            )
            worker.completed.connect(lambda *args: captured.append(args))
            worker.run()
            page = captured[0][2]
            self.assertEqual(page.total, 0)
            self.assertEqual(page.items, [])


if __name__ == "__main__":
    unittest.main()
