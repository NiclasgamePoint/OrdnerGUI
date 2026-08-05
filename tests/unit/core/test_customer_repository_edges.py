from __future__ import annotations

import sqlite3
from unittest.mock import patch

from app.core.config import CustomerRecognitionOptions
from app.core.customer_models import Contact, Customer, CustomerProject
from app.core.customer_recognition_models import (
    ExtractionEvidence,
    RecognitionCandidate,
    RecognitionStats,
)
from app.core.customer_repository import CustomerRepository
from app.core.folder_structure import ProjectRoot
from app.core.search_models import SearchSort
from tests.base.test_case import PapaGuiTestCase


class CustomerRepositoryEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path / "customers.db"
        self.repository = CustomerRepository(self.path)
        self.addCleanup(self.repository.close)
        self.customer = self.repository.save(Customer(
            display_name="Muster", city="Berlin", email="old@example.test",
            folder_paths=[str(self.temp_path / "Service" / "2026" / "Muster")],
            service_types=["Service"], contacts=[Contact(name="Max")],
        ))

    def test_folder_lookup_find_empty_readonly_and_windows_case_branch(self):
        self.assertIn("Unternehmen", self.repository.list_customer_types())
        folder = self.customer.folder_paths[0]
        self.assertEqual(self.repository.get_by_folder(folder, False).id, self.customer.id)
        self.assertEqual(
            self.repository.get_by_folder(f"{folder}/child").id, self.customer.id
        )
        self.assertIsNone(self.repository.get_by_folder("/missing", False))
        self.assertEqual(self.repository.find_by_name(""), [])
        self.assertEqual(self.repository.find_by_identity("Muster", "Berlin")[0].id, self.customer.id)
        self.assertIn(self.customer.id, self.repository.customer_ids_within_folder(folder))
        with (
            patch("app.core.customer_repository.os.name", "nt"),
            patch.object(self.repository, "_folder_key", return_value=f"{folder}/child"),
            patch.object(self.repository, "find_project_by_folder", return_value=None),
        ):
            self.repository.get_by_folder(f"{folder}/child")
        readonly = CustomerRepository(self.path, readonly=True)
        self.assertEqual(readonly.get(self.customer.id).display_name, "Muster")
        readonly.close()

    def test_journal_empty_missing_update_delete_and_renumber(self):
        self.assertIsNone(self.repository.add_journal_entry(self.customer.id, " "))
        with self.assertRaises(ValueError):
            self.repository.add_journal_entry(999, "body")
        first = self.repository.add_journal_entry(self.customer.id, "one", " First ")
        second = self.repository.add_journal_entry(self.customer.id, "two")
        self.assertIsNone(self.repository.update_journal_entry(self.customer.id, first.id, ""))
        self.assertIsNone(self.repository.update_journal_entry(self.customer.id, 999, "body"))
        self.assertFalse(self.repository.delete_journal_entry(self.customer.id, 999))
        self.assertTrue(self.repository.delete_journal_entry(self.customer.id, first.id))
        self.assertEqual(
            self.repository.list_journal_entries(self.customer.id)[0].entry_number, 1
        )
        self.assertEqual(second.entry_number, 2)

    def test_service_project_creation_missing_owner_conflict_and_helpers(self):
        unknown = self.repository.upsert_service_type("")
        self.assertEqual(unknown.name, "Unbekannt")
        project = CustomerProject(
            service_type="Service", folder_path=str(self.temp_path / "project"),
            project_label="Project",
        )
        with self.assertRaisesRegex(ValueError, "benötigt"):
            self.repository.upsert_project_from_root(project)
        project.customer_id = self.customer.id
        stored = self.repository.upsert_project_from_root(project)
        other = self.repository.save(Customer(display_name="Other"))
        project.customer_id = other.id
        with self.assertRaisesRegex(ValueError, "anderen Kunden"):
            self.repository.upsert_project_from_root(project)
        self.assertIsNone(self.repository.get_project(999))
        self.assertIsNone(self.repository.find_project_by_folder("/missing", False))

        root = ProjectRoot(
            path="/Service/2026/Root", relative_path="Service/2026/Root",
            service_type="Service", year=2026, customer_label="Root",
            customer_name="Root", city="Berlin", recognition_key="root",
        )
        converted = self.repository._project_from_root(root)
        self.assertEqual(converted.year, 2026)
        dictionary = self.repository._project_from_root({
            "folder_path": "/Unknown/Project", "year": "bad"
        })
        self.assertIsNone(dictionary.year)
        inferred = self.repository._infer_project_from_folder(
            "/Service/2025/Customer, City"
        )
        self.assertEqual((inferred.service_type, inferred.year, inferred.project_city), ("Service", 2025, "City"))
        item = self.repository._candidate_project(
            RecognitionCandidate("key", "Name", "", ["/NoYear"], [], [2024]),
            "/NoYear", 0,
        )
        self.assertEqual(item.year, 2024)
        self.assertEqual(stored.customer_id, self.customer.id)

    def test_field_and_contact_suggestion_validation_and_resolution_edges(self):
        self.assertIsNone(self.repository.apply_project_suggestion(
            self.customer.id, None, "", "value"
        ))
        self.assertIsNone(self.repository.apply_project_suggestion(
            self.customer.id, None, "city", ""
        ))
        suggestion = self.repository.apply_project_suggestion(
            self.customer.id, None, "city", "Hamburg", confidence=2,
        )
        same = self.repository.apply_project_suggestion(
            self.customer.id, None, "city", "Hamburg"
        )
        self.assertEqual(same.id, suggestion.id)
        self.repository.resolve_data_suggestion(suggestion.id, False)
        self.assertIsNone(self.repository.apply_project_suggestion(
            self.customer.id, None, "city", "Hamburg"
        ))
        with self.assertRaisesRegex(ValueError, "nicht mehr offen"):
            self.repository.resolve_data_suggestion(suggestion.id, True)

        unsupported = self.repository.apply_project_suggestion(
            self.customer.id, None, "unsupported", "value"
        )
        with self.assertRaisesRegex(ValueError, "nicht unterstützt"):
            self.repository.resolve_data_suggestion(unsupported.id, True)
        entity = self.repository.apply_project_suggestion(
            self.customer.id, None, "entity_type", "invalid"
        )
        with self.assertRaisesRegex(ValueError, "Kundentyp"):
            self.repository.resolve_data_suggestion(entity.id, True)

        contact = Contact(name="Max", role="Lead", email="max@example.test")
        card = self.repository.apply_contact_suggestion(
            self.customer.id, None, contact, confidence=-1,
        )
        self.repository.resolve_data_suggestion(card.id, True)
        accepted = self.repository.get(self.customer.id)
        self.assertEqual(accepted.contacts[0].role, "Lead")
        self.assertIsNone(self.repository.apply_contact_suggestion(
            self.customer.id, None, contact
        ))

    def test_recognition_decisions_pending_runs_search_and_cleanup_empty(self):
        candidate = RecognitionCandidate(
            "key", "Candidate", "", ["/Service/2026/Candidate"],
            ["Service"], [2026], reason="review",
        )
        self.assertIsNone(self.repository.get_recognition_decision("missing"))
        self.assertFalse(self.repository.has_previous_recognition_decision("key", "signature"))
        self.repository.replace_pending_recognition_cases([candidate])
        self.assertEqual(self.repository.pending_recognition_count(), 1)
        self.assertEqual(self.repository.list_pending_recognition_cases()[0].display_name, "Candidate")
        self.repository.save_recognition_decision(candidate.signature, "ignore")
        self.assertEqual(self.repository.get_recognition_decision(candidate.signature)["action"], "ignore")
        self.assertTrue(self.repository.has_previous_recognition_decision("key", "other"))
        stats = RecognitionStats(detected=1, error="error")
        self.repository.record_recognition_run(stats)
        self.assertEqual(self.repository.last_recognition_run()["error"], "error")
        self.assertEqual(self.repository.search("", sort_order=SearchSort.DATE), [])
        self.assertTrue(self.repository.search("Muster", sort_order=SearchSort.ALPHABETICAL))
        self.repository.set_blacklist_suggestion_status(999, "dismissed")
        result = self.repository.cleanup_automatic_blacklisted_values(
            CustomerRecognitionOptions()
        )
        self.assertEqual(result, {"fields": 0, "contacts": 0})

    def test_merge_group_and_auto_merge_matching_edge_cases(self):
        with self.assertRaises(ValueError):
            self.repository._merge_customer_group([])
        with self.assertRaisesRegex(ValueError, "keine ID"):
            self.repository._merge_customer_group([Customer(display_name="Unsaved")])

        cursor = self.repository.connection.execute(
            "INSERT INTO customers(folder_path,display_name,entity_type,company,email,phone,street,postal_code,city) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "customer://second", "Muster", "Unternehmen", "Company",
                "old@example.test", "123", "Street", "1", "",
            ),
        )
        second_id = int(cursor.lastrowid)
        self.repository._replace_contacts(second_id, [Contact(name="Other", phone="123")])
        self.repository._replace_services(second_id, ["Other Service"])
        self.repository._replace_folders(second_id, ["customer://second"])
        self.repository._replace_notes(second_id, ["note"])
        self.repository._replace_tags(second_id, ["tag"])
        self.repository.connection.commit()
        first = self.repository.get(self.customer.id)
        second = self.repository.get(second_id)
        merged = self.repository._merge_customer_group(
            [first, second], preferred_id=self.customer.id
        )
        self.repository.connection.commit()
        self.assertEqual(merged.id, self.customer.id)
        self.assertIsNone(self.repository.get(second_id))
        self.assertEqual(
            self.repository.connection.execute(
                "SELECT COUNT(*) FROM customer_merge_log WHERE absorbed_id=?",
                (second_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.repository._merge_customer_group([self.repository.get(self.customer.id)]).id,
            self.customer.id,
        )

        self.assertFalse(self.repository._is_auto_merge_match(
            Customer(display_name="A"), Customer(display_name="B")
        ))
        self.assertFalse(self.repository._is_auto_merge_match(
            Customer(display_name="A", city="Berlin"),
            Customer(display_name="A", city="Hamburg"),
        ))
        self.assertTrue(self.repository._is_auto_merge_match(
            Customer(display_name="A", city="Berlin"),
            Customer(display_name="A", city="Berlin"),
        ))
        self.assertFalse(self.repository._is_auto_merge_match(
            Customer(display_name="A", city="Berlin"), Customer(display_name="A")
        ))
        self.assertTrue(self.repository._is_auto_merge_match(
            Customer(display_name="A", city="Berlin", email="x@y"),
            Customer(display_name="A", email="x@y"),
        ))
        self.assertTrue(self.repository._is_auto_merge_match(
            Customer(display_name="A", company="C", phone="1", street="S", postal_code="1"),
            Customer(display_name="A", company="C", phone="1", street="S", postal_code="1"),
        ))

        combined = self.repository._combine_customer_data(
            Customer(
                id=1, folder_path="customer://one", folder_paths=["customer://one"],
                display_name="Name", contacts=[Contact(name="")], notes=[" Note "],
            ),
            [Customer(folder_paths=[str(self.temp_path / "physical")], notes=["note"], tags=["Tag"])],
        )
        self.assertFalse(combined.folder_path.startswith("customer://"))
        self.assertEqual(combined.notes, ["Note"])

    def test_apply_recognition_and_automatic_contact_provenance_paths(self):
        with self.assertRaisesRegex(ValueError, "existiert nicht"):
            self.repository.apply_recognition_candidate(
                RecognitionCandidate("x", "X", "", [], [], []), 999
            )
        with self.assertRaisesRegex(ValueError, "bereits"):
            self.repository.apply_recognition_candidate(
                RecognitionCandidate(
                    "x", "X", "", [self.customer.folder_paths[0]], [], []
                )
            )

        automatic = ExtractionEvidence(
            "contact_name", "Anna Person", "anna person", "/new", "excerpt",
            0, "rule", 0.95, True,
        )
        weak = ExtractionEvidence(
            "city", "Munich", "munich", "/new", "excerpt", 0, "rule", 0.7,
            False,
        )
        new_folder = str(self.temp_path / "Service" / "2024" / "New")
        created = self.repository.apply_recognition_candidate(RecognitionCandidate(
            "new", "New", "Munich", [new_folder], ["Service", ""], [2024],
            email="new@example.test", street="Street", postal_code="1",
            contacts=[Contact(name="Anna Person", email="anna@example.test")],
            evidence=[automatic, weak],
        ))
        self.assertEqual(created.city, "Munich")
        self.assertTrue(self.repository.list_data_suggestions(created.id))

        conflicting = RecognitionCandidate(
            "existing", "Muster", "Hamburg", [self.customer.folder_paths[0]],
            ["Service"], [2026], email="different@example.test",
            street="Other", postal_code="2",
        )
        updated = self.repository.apply_recognition_candidate(
            conflicting, self.customer.id
        )
        self.assertEqual(updated.email, "old@example.test")
        self.assertTrue(self.repository.list_data_suggestions(self.customer.id))

        self.assertEqual(self.repository._upsert_automatic_contact(
            self.customer.id, Contact(), conflicting
        ), 0)
        self.assertEqual(self.repository._upsert_automatic_contact(
            self.customer.id, Contact(name="Max", email="new@x"), conflicting
        ), 0)

        surname = self.repository.connection.execute(
            "INSERT INTO contacts(customer_id,name) VALUES(?,?)",
            (self.customer.id, "Person"),
        )
        surname_id = int(surname.lastrowid)
        self.repository.connection.execute(
            "INSERT INTO automatic_field_sources(owner_type,owner_id,field_name,value,normalized_value,source_path,confidence) "
            "VALUES('contact',?,'name','Person','person','/source',1)",
            (surname_id,),
        )
        changed = self.repository._upsert_automatic_contact(
            self.customer.id,
            Contact(name="Anna Person", email="anna@x", phone="123"),
            RecognitionCandidate(
                "key", "Name", "", [], [], [], evidence=[automatic]
            ),
        )
        self.assertEqual(changed, 3)
        inserted = self.repository._upsert_automatic_contact(
            self.customer.id, Contact(name="Brand New", email="brand@x"),
            RecognitionCandidate("key", "Name", "", [], [], []),
        )
        self.assertEqual(inserted, 2)

        self.repository._accept_contact_name(self.customer.id, "Max")
        self.repository._accept_contact_name(self.customer.id, "Another Person")
        self.repository._accept_contact_name(self.customer.id, "Full Person")

    def test_legacy_schema_migrations_cover_journal_and_suggestion_rows(self):
        legacy_path = self.temp_path / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            "CREATE TABLE customer_journal_entries("
            "id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,body TEXT NOT NULL,"
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
            "INSERT INTO customer_journal_entries(customer_id,body) VALUES(1,'one'),(1,'two');"
            "CREATE TABLE customer_data_suggestions("
            "id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,project_id INTEGER,"
            "field_name TEXT NOT NULL,suggested_value TEXT NOT NULL,source_path TEXT NOT NULL DEFAULT '',"
            "status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
            "INSERT INTO customer_data_suggestions(customer_id,field_name,suggested_value) "
            "VALUES(1,'contact_name','Max'),(1,'city','Berlin');"
        )
        connection.commit()
        connection.close()
        legacy = CustomerRepository(legacy_path)
        self.addCleanup(legacy.close)
        entries = legacy.list_journal_entries(1)
        self.assertEqual([entry.entry_number for entry in entries], [1, 2])
        suggestions = legacy.list_data_suggestions(1)
        self.assertEqual(suggestions[0].suggestion_type, "contact")
        self.assertTrue(suggestions[1].fingerprint)

    def test_blacklist_cleanup_all_field_kinds_and_observation_pruning(self):
        with self.assertRaises(ValueError):
            self.repository.set_blacklist_suggestion_status(1, "invalid")
        self.repository.connection.execute(
            "INSERT INTO blacklist_suggestions(value_type,normalized_value,value) VALUES('email','old','old')"
        )
        self.repository.record_extraction_observations([], threshold=2)
        self.assertEqual(self.repository.list_blacklist_suggestions(), [])

        customer_id = self.customer.id
        self.repository.connection.execute(
            "UPDATE customers SET phone='123',street='Blocked Street' WHERE id=?",
            (customer_id,),
        )
        contact_id = int(self.repository.connection.execute(
            "INSERT INTO contacts(customer_id,name,email,phone) VALUES(?,?,?,?)",
            (customer_id, "Blocked Name", "blocked@example.test", "456"),
        ).lastrowid)
        sources = [
            ("customer", customer_id, "phone", "123", "123"),
            ("customer", customer_id, "street", "Blocked Street", "blocked street"),
            ("contact", contact_id, "email", "blocked@example.test", "blocked@example.test"),
            ("contact", contact_id, "phone", "456", "456"),
            ("contact", contact_id, "name", "Blocked Name", "blocked name"),
            ("customer", customer_id, "unknown", "value", "value"),
        ]
        self.repository.connection.executemany(
            "INSERT INTO automatic_field_sources(owner_type,owner_id,field_name,value,normalized_value) "
            "VALUES(?,?,?,?,?)",
            sources,
        )
        self.repository.connection.commit()
        result = self.repository.cleanup_automatic_blacklisted_values(
            CustomerRecognitionOptions(
                phone_blacklist="123\n456", address_blacklist="Blocked Street",
                email_blacklist="blocked@example.test", name_blacklist="Blocked Name",
            )
        )
        self.assertGreaterEqual(result["fields"], 2)
        self.assertGreaterEqual(result["contacts"], 1)

    def test_pending_field_update_contact_empty_and_missing_scan_customer(self):
        suggestion = self.repository.apply_project_suggestion(
            self.customer.id, None, "city", "Hamburg"
        )
        updated = self.repository.apply_project_suggestion(
            self.customer.id, None, "city", "Hamburg", source_path="/source",
            excerpt="excerpt", rule="rule", confidence=0.8,
        )
        self.assertEqual(updated.id, suggestion.id)
        self.assertEqual(updated.source_path, "/source")
        self.assertIsNone(self.repository.apply_contact_suggestion(
            self.customer.id, None, Contact()
        ))
        self.assertIsNone(self.repository.apply_contact_suggestion(
            self.customer.id, None, Contact(name="Max")
        ))
        with self.assertRaisesRegex(ValueError, "existiert nicht"):
            self.repository.apply_contact_scan_candidate(
                999, RecognitionCandidate("x", "X", "", [], [], [])
            )

        def evidence(field, value, confidence=0.95, automatic=True):
            return ExtractionEvidence(
                field, value, value.casefold(), self.customer.folder_paths[0],
                "excerpt", 0, "rule", confidence, automatic,
            )

        contact = Contact(name="Scan Person", email="scan@example.test", phone="987")
        scan_candidate = RecognitionCandidate(
            "scan", "Muster", "", [self.customer.folder_paths[0]], [], [],
            contacts=[contact],
            evidence=[
                evidence("contact_name", "Scan Person"),
                evidence("email", "scan@example.test"),
                evidence("phone", "987"),
                evidence("street", "New Street"),
                evidence("city", "Pending City", 0.7, False),
                evidence("unsupported", "ignored", 0.7, False),
                evidence("city", "Pending City", 0.6, False),
            ],
        )
        stats = self.repository.apply_contact_scan_candidate(
            self.customer.id, scan_candidate
        )
        self.assertGreater(stats.found_fields, 0)
        self.assertGreater(stats.pending_fields, 0)

    def test_add_folder_validations_physical_branch_and_service_insertion(self):
        with self.assertRaises(ValueError):
            self.repository.add_folder_to_customer(self.customer.id, " ")
        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            self.repository.add_folder_to_customer(999, "/folder")
        new_folder = str(self.temp_path / "Service2" / "2025" / "Muster")
        updated = self.repository.add_folder_to_customer(
            self.customer.id, new_folder, "Service2"
        )
        self.assertIn(new_folder, updated.folder_paths)
        self.repository.add_folder_to_customer(self.customer.id, new_folder, "Service2")
        virtual = self.repository.save(Customer(display_name="Virtual"))
        physical = str(self.temp_path / "Virtual")
        updated = self.repository.add_folder_to_customer(virtual.id, physical)
        self.assertEqual(updated.folder_path, physical)
        other = self.repository.save(Customer(display_name="Other Owner"))
        with self.assertRaisesRegex(ValueError, "bereits"):
            self.repository.add_folder_to_customer(other.id, physical)

    def test_automatic_exact_contact_fill_accept_surname_and_public_merge(self):
        max_row = self.repository.connection.execute(
            "SELECT id FROM contacts WHERE customer_id=? AND name='Max'", (self.customer.id,)
        ).fetchone()
        max_id = int(max_row[0])
        self.repository.connection.execute(
            "INSERT INTO automatic_field_sources(owner_type,owner_id,field_name,value,normalized_value) "
            "VALUES('contact',?,'name','Max','max')", (max_id,)
        )
        changed = self.repository._upsert_automatic_contact(
            self.customer.id, Contact(name="Max", email="filled@example.test"),
            RecognitionCandidate("key", "Muster", "", [], [], []),
        )
        self.assertEqual(changed, 1)

        row = self.repository.connection.execute(
            "INSERT INTO contacts(customer_id,name) VALUES(?, 'Surname')", (self.customer.id,)
        )
        surname_id = int(row.lastrowid)
        self.repository.connection.execute(
            "INSERT INTO automatic_field_sources(owner_type,owner_id,field_name,value,normalized_value) "
            "VALUES('contact',?,'name','Surname','surname')", (surname_id,)
        )
        self.repository._accept_contact_name(self.customer.id, "Full Surname")
        self.assertEqual(
            self.repository.connection.execute(
                "SELECT name FROM contacts WHERE id=?", (surname_id,)
            ).fetchone()[0],
            "Full Surname",
        )

        for city in ("Berlin", ""):
            self.repository.connection.execute(
                "INSERT INTO customers(folder_path,display_name,entity_type,email,city) "
                "VALUES(?,?,?,?,?)",
                (f"customer://duplicate-{city}", "Duplicate", "Unternehmen", "same@x", city),
            )
        self.repository.connection.commit()
        mapping = self.repository.merge_duplicate_customers_by_name()
        self.assertEqual(len(mapping), 1)
        empty = self.repository._combine_customer_data(Customer(display_name="Empty"), [])
        self.assertTrue(empty.folder_path.startswith("customer://"))

    def test_project_conversion_empty_fields_and_legacy_project_migration(self):
        project = self.repository.upsert_project_from_root(
            {"path": "/Service/2027/Dictionary", "service_type": "Service"},
            customer_id=self.customer.id,
        )
        self.assertEqual(project.customer_id, self.customer.id)
        self.assertEqual(
            self.repository._infer_project_from_folder("/Service/2025").year, 2025
        )
        candidate_project = self.repository._candidate_project(
            RecognitionCandidate("empty", "Empty", "", [""], [], []), "", 0
        )
        self.assertTrue(candidate_project.project_label)

        cursor = self.repository.connection.execute(
            "INSERT INTO customers(folder_path,display_name,entity_type,company,email,phone,street,postal_code,city) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("/Legacy/2024/Customer", "Legacy", "Unternehmen", "", "", "", "", "", ""),
        )
        legacy_id = int(cursor.lastrowid)
        self.repository.connection.execute(
            "INSERT INTO customer_services(customer_id,name) VALUES(?,?)",
            (legacy_id, "Legacy Service"),
        )
        self.repository._migrate_legacy_projects()
        self.assertTrue(self.repository.list_projects_for_customer(legacy_id))

        second = self.repository.connection.execute(
            "INSERT INTO customers(folder_path,display_name,entity_type,company,email,phone,street,postal_code,city) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("/Legacy/2023/Conflict", "Conflict", "Unternehmen", "", "", "", "", "", ""),
        )
        with patch.object(
            self.repository, "upsert_project_from_root", side_effect=ValueError("conflict")
        ):
            self.repository._migrate_legacy_projects()
        self.assertIsNotNone(second.lastrowid)

    def test_contact_suggestion_no_updates_deleted_customer_and_contact_migration(self):
        self.repository._accept_contact_suggestion(
            self.customer.id, Contact(name="Max")
        )
        suggestion = self.repository.apply_project_suggestion(
            self.customer.id, None, "city", "Hamburg"
        )
        original_get = self.repository.get
        with patch.object(self.repository, "get", return_value=None):
            with self.assertRaisesRegex(ValueError, "existiert nicht mehr"):
                self.repository.resolve_data_suggestion(suggestion.id, False)
        self.assertIsNotNone(original_get(self.customer.id))

        self.repository.connection.execute(
            "INSERT INTO customer_data_suggestions("
            "customer_id,field_name,suggested_value,suggestion_type,contact_name,"
            "contact_role,contact_email,contact_phone,fingerprint) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (self.customer.id, "contact", "", "contact", "Jane", "Lead", "j@x", "1", ""),
        )
        self.repository._migrate_data_suggestions()
        fingerprint = self.repository.connection.execute(
            "SELECT fingerprint FROM customer_data_suggestions WHERE contact_name='Jane'"
        ).fetchone()[0]
        self.assertTrue(fingerprint)

    def test_recognition_creation_failures_conflicts_and_empty_address_fill(self):
        other = self.repository.save(Customer(display_name="Other"))
        with patch.object(
            self.repository, "customer_ids_within_folder", return_value=[other.id]
        ), patch.object(self.repository, "get_by_folder", return_value=None):
            with self.assertRaisesRegex(ValueError, "anderen Kunden"):
                self.repository.apply_recognition_candidate(
                    RecognitionCandidate("x", "X", "", ["/new"], [], []),
                    self.customer.id,
                )

        with patch.object(self.repository, "save", side_effect=RuntimeError("save failed")):
            with self.assertRaisesRegex(RuntimeError, "save failed"):
                self.repository.apply_recognition_candidate(
                    RecognitionCandidate("x", "X", "", [], [], [])
                )
        with patch.object(self.repository, "save", return_value=Customer(display_name="No Id")):
            with self.assertRaisesRegex(RuntimeError, "keine ID"):
                self.repository.apply_recognition_candidate(
                    RecognitionCandidate("x", "X", "", [], [], [])
                )

        address_customer = self.repository.save(Customer(display_name="Address"))
        updated = self.repository.apply_recognition_candidate(
            RecognitionCandidate(
                "address", "Address", "Berlin", [], [], [],
                street="Street", postal_code="1",
            ),
            address_customer.id,
        )
        self.assertEqual((updated.street, updated.postal_code, updated.city), ("Street", "1", "Berlin"))

        with patch.object(self.repository, "get", side_effect=[address_customer, None]):
            with self.assertRaisesRegex(RuntimeError, "nicht geladen"):
                self.repository.apply_recognition_candidate(
                    RecognitionCandidate("gone", "Address", "", [], [], []),
                    address_customer.id,
                )

    def test_cleanup_name_contact_merge_and_collection_branch_edges(self):
        contact_id = int(self.repository.connection.execute(
            "INSERT INTO contacts(customer_id,name,email,phone) VALUES(?,?,?,?)",
            (self.customer.id, "Delete Me", "", ""),
        ).lastrowid)
        self.repository.connection.execute(
            "INSERT INTO automatic_field_sources(owner_type,owner_id,field_name,value,normalized_value) "
            "VALUES('contact',?,'name','Delete Me','delete me')",
            (contact_id,),
        )
        result = self.repository.cleanup_automatic_blacklisted_values(
            CustomerRecognitionOptions(name_blacklist="Delete Me")
        )
        self.assertEqual(result["contacts"], 1)

        blank = self.repository.connection.execute(
            "INSERT INTO customers(folder_path,display_name,entity_type,company,email,phone,street,postal_code,city) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("customer://blank", "", "Unternehmen", "", "", "", "", "", ""),
        )
        self.repository.connection.commit()
        self.repository.merge_duplicate_customers_by_name()
        self.assertIsNotNone(blank.lastrowid)

        customers = [
            Customer(id=101, display_name="Cluster", city="A"),
            Customer(id=102, display_name="Cluster", city="B"),
        ]
        with patch.object(self.repository, "list_customers", return_value=customers):
            self.assertEqual(self.repository.merge_duplicate_customers_by_name(), {})
        mergeable = [
            Customer(id=201, display_name="Merge", email="same@x"),
            Customer(id=202, display_name="Merge", email="same@x"),
        ]
        with patch.object(self.repository, "list_customers", return_value=mergeable), \
                patch.object(self.repository, "_is_auto_merge_match", return_value=True), \
                patch.object(
                    self.repository, "_merge_customer_group",
                    return_value=Customer(display_name="No id"),
                ):
            self.assertEqual(self.repository.merge_duplicate_customers_by_name(), {})

        automatic_city = ExtractionEvidence(
            "city", "Other City", "other city", "/source", "excerpt", 0,
            "rule", 0.9, True,
        )
        self.repository.apply_contact_scan_candidate(
            self.customer.id,
            RecognitionCandidate(
                "current", "Muster", "", [], [], [], evidence=[automatic_city]
            ),
        )

        combined = self.repository._combine_customer_data(
            Customer(display_name="Primary", folder_paths=["", "customer://one"]),
            [Customer(folder_paths=["customer://one"])],
        )
        self.assertTrue(combined.folder_path)
        saved = self.repository.save(Customer(
            display_name="Repeated", folder_paths=["/same", "/same"], tags=["", "Tag"]
        ))
        self.assertEqual(len(saved.folder_paths), 1)

    def test_repository_defensive_database_and_recognition_race_paths(self):
        class ConnectionProxy:
            def __init__(self, connection, marker):
                self.connection = connection
                self.marker = marker

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def execute(self, sql, parameters=()):
                if self.marker in " ".join(sql.split()):
                    raise sqlite3.IntegrityError("forced")
                return self.connection.execute(sql, parameters)

            def __getattr__(self, name):
                return getattr(self.connection, name)

        real_connection = self.repository.connection
        self.repository.connection = ConnectionProxy(
            real_connection, "INSERT OR IGNORE INTO customer_folders"
        )
        try:
            with self.assertRaisesRegex(ValueError, "eindeutig"):
                self.repository.add_folder_to_customer(
                    self.customer.id, "/Forced/2026/Failure", "Service"
                )
        finally:
            self.repository.connection = real_connection

        with patch.object(
            self.repository, "get", side_effect=[self.customer, None]
        ), patch.object(self.repository, "upsert_project_from_root"):
            with self.assertRaisesRegex(RuntimeError, "nicht geladen"):
                self.repository.add_folder_to_customer(
                    self.customer.id, "/Forced/2026/Gone", "Service"
                )

        disappearing = self.repository.save(Customer(display_name="Disappearing"))

        def delete_during_preflight(_folder):
            real_connection.execute("DELETE FROM customers WHERE id=?", (disappearing.id,))
            real_connection.commit()
            return []

        with patch.object(
            self.repository, "customer_ids_within_folder", side_effect=delete_during_preflight
        ), patch.object(self.repository, "get_by_folder", return_value=None):
            with self.assertRaisesRegex(ValueError, "existiert nicht mehr"):
                self.repository.apply_recognition_candidate(
                    RecognitionCandidate("gone", "Gone", "", ["/gone"], [], []),
                    disappearing.id,
                )

        other = self.repository.save(Customer(display_name="Race Owner"))
        with patch.object(self.repository, "customer_ids_within_folder", return_value=[]), \
                patch.object(self.repository, "get_by_folder", side_effect=[None, other]):
            with self.assertRaisesRegex(ValueError, "Race Owner"):
                self.repository.apply_recognition_candidate(
                    RecognitionCandidate("race", "Muster", "", ["/race"], [], []),
                    self.customer.id,
                )

        self.repository.connection = ConnectionProxy(
            real_connection, "INSERT OR IGNORE INTO customer_folders"
        )
        try:
            with patch.object(self.repository, "customer_ids_within_folder", return_value=[]), \
                    patch.object(self.repository, "get_by_folder", return_value=None):
                with self.assertRaisesRegex(ValueError, "nicht eindeutig"):
                    self.repository.apply_recognition_candidate(
                        RecognitionCandidate("integrity", "Muster", "", ["/integrity"], [], []),
                        self.customer.id,
                    )
        finally:
            self.repository.connection = real_connection

    def test_remaining_merge_cleanup_project_and_save_branches(self):
        with patch.object(
            self.repository, "_infer_project_from_folder",
            return_value=CustomerProject(folder_path="", project_label=""),
        ):
            project = self.repository._candidate_project(
                RecognitionCandidate("x", "X", "", ["fallback"], [], []),
                "fallback", 0,
            )
        self.assertEqual(project.project_label, "fallback")

        candidate = RecognitionCandidate("projects", "Muster", "", ["/project"], [], [])
        with patch.object(self.repository, "customer_ids_within_folder", return_value=[]), \
                patch.object(self.repository, "get_by_folder", return_value=None), \
                patch.object(self.repository, "upsert_project_from_root", return_value=None):
            self.repository.apply_recognition_candidate(candidate, self.customer.id)

        row = self.repository.connection.execute(
            "SELECT id FROM contacts WHERE customer_id=? AND name='Max'", (self.customer.id,)
        ).fetchone()
        contact_id = int(row[0])
        self.repository.connection.execute(
            "INSERT OR IGNORE INTO automatic_field_sources("
            "owner_type,owner_id,field_name,value,normalized_value) "
            "VALUES('contact',?,'name','Max','max')", (contact_id,)
        )
        unchanged = self.repository._upsert_automatic_contact(
            self.customer.id, Contact(name="Max"),
            RecognitionCandidate("same", "Muster", "", [], [], []),
        )
        self.assertEqual(unchanged, 0)

        self.repository.connection.execute(
            "INSERT INTO extracted_value_observations(value_type,normalized_value,value,folder_path,source_path) "
            "VALUES('email','same','Same','/one','/source')"
        )
        self.repository.connection.execute(
            "INSERT INTO extracted_value_observations(value_type,normalized_value,value,folder_path,source_path) "
            "VALUES('email','same','Same','/two','/source')"
        )
        self.repository.connection.execute(
            "INSERT INTO blacklist_suggestions(value_type,normalized_value,value) "
            "VALUES('email','same','Same')"
        )
        self.repository.record_extraction_observations([], threshold=2)
        self.assertTrue(self.repository.list_blacklist_suggestions())

        self.repository.connection.executemany(
            "INSERT INTO automatic_field_sources(owner_type,owner_id,field_name,value,normalized_value) "
            "VALUES(?,?,?,?,?)",
            [
                ("customer", 99999, "email", "blocked@x", "blocked@x"),
                ("contact", 99999, "email", "blocked@x", "blocked@x"),
            ],
        )
        self.repository.cleanup_automatic_blacklisted_values(
            CustomerRecognitionOptions(email_blacklist="blocked@x")
        )

        virtual_primary = Customer(
            id=self.customer.id, display_name="Muster",
            folder_paths=["customer://virtual"], folder_path="customer://virtual",
        )
        absorbed = Customer(id=999, display_name="Muster")
        with patch.object(self.repository, "get", side_effect=[virtual_primary, None]):
            with patch.object(self.repository, "_sync_legacy_project_links"):
                try:
                    self.repository._merge_customer_group([virtual_primary, absorbed])
                except sqlite3.IntegrityError:
                    pass

        primary = self.repository.get(self.customer.id)
        physical = Customer(
            id=998, display_name=primary.display_name,
            folder_paths=["/Physical/2026/Folder"], email=primary.email,
        )
        with patch.object(
            self.repository, "upsert_project_from_root", side_effect=ValueError("conflict")
        ):
            try:
                self.repository._merge_customer_group([primary, physical])
            except sqlite3.IntegrityError:
                pass

        virtual = self.repository.save(Customer(
            display_name="Virtual Save", folder_paths=["customer://explicit"]
        ))
        self.assertTrue(virtual.folder_path.startswith("customer://"))

        current = self.repository.get(self.customer.id)
        duplicate = Customer(id=777, display_name=current.display_name, email=current.email)
        with patch.object(
            self.repository, "find_by_name",
            side_effect=[[], [current, duplicate]],
        ), patch.object(self.repository, "_is_auto_merge_match", return_value=True), \
                patch.object(
                    self.repository, "_merge_customer_group",
                    return_value=Customer(id=current.id, display_name=current.display_name),
                ):
            self.repository.save(Customer(display_name=current.display_name), commit=False)
