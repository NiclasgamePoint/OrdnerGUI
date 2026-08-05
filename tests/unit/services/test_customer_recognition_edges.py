from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.config import CustomerRecognitionOptions
from app.core.customer_models import Contact, Customer
from app.core.customer_recognition_models import (
    ContactScanStats,
    RecognitionCandidate,
    RecognitionStats,
)
from app.services.customer_recognition import CustomerRecognitionService, RecognitionBlacklist
from app.services.customer_suggestion import CustomerSuggestion
from tests.base.test_case import PapaGuiTestCase


def candidate(**changes):
    values = {
        "recognition_key": "customer", "display_name": "Customer", "city": "Berlin",
        "folder_paths": ["/Service/2026/Customer"], "service_types": ["Service"],
        "years": [2026],
    }
    values.update(changes)
    return RecognitionCandidate(**values)


class CustomerRecognitionEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.index_path = self.temp_path / "index.db"
        self.customer_path = self.temp_path / "customers.db"
        self.service = CustomerRecognitionService(
            self.index_path, self.customer_path, CustomerRecognitionOptions()
        )

    def test_blacklist_filters_fields_contacts_evidence_and_empty_addresses(self):
        options = CustomerRecognitionOptions(
            email_blacklist="blocked@example.test", phone_blacklist="123",
            name_blacklist="Bad Name", address_blacklist="Bad Street",
            text_blacklist="forbidden",
        )
        blacklist = RecognitionBlacklist(options)
        self.assertEqual(blacklist.normalize_phone(" +49 (12) "), "+4912")
        self.assertFalse(blacklist.contains_fragment(""))
        self.assertFalse(blacklist.address_blocked(""))
        suggestion = CustomerSuggestion(
            email="blocked@example.test", phone="123", street="Bad Street 1",
            postal_code="12345", city="Town",
            contacts=[
                Contact(name="Bad Name", email="x"),
                Contact(name="Kontakt", email="blocked@example.test", phone="123"),
                Contact(name="Good", email="good@example.test", phone="456"),
            ],
        )
        filtered = blacklist.filter_suggestion(suggestion)
        self.assertEqual(filtered.email, "")
        self.assertEqual(filtered.phone, "")
        self.assertEqual([item.name for item in filtered.contacts], ["Good"])

    def test_rescan_validation_and_pending_content_messages_cleanup(self):
        with self.assertRaisesRegex(ValueError, "Suchindex"):
            self.service.rescan_customer_contacts(1)
        self.index_path.touch()
        manager = Mock()
        repository = Mock()
        with (
            patch("app.services.customer_recognition.IndexManager", return_value=manager),
            patch("app.services.customer_recognition.CustomerRepository", return_value=repository),
        ):
            repository.get.return_value = None
            with self.assertRaisesRegex(ValueError, "existiert nicht"):
                self.service.rescan_customer_contacts(1)
            repository.get.return_value = Customer(id=1)
            repository.list_projects_for_customer.return_value = []
            with self.assertRaisesRegex(ValueError, "keine indexierten"):
                self.service.rescan_customer_contacts(1)
            project = SimpleNamespace(folder_path="/folder", service_type="Service", year=None)
            repository.list_projects_for_customer.return_value = [project]
            manager.indexed_documents_for_folder.return_value = []
            with self.assertRaisesRegex(ValueError, "keine.*Dokumente"):
                self.service.rescan_customer_contacts(1)
            manager.indexed_documents_for_folder.return_value = [{"content": "text"}]
            self.service._suggestions = Mock()
            self.service._suggestions.suggest_from_documents.return_value = CustomerSuggestion()
            self.service._suggestions._mark_automatic_evidence.return_value = []
            self.service._suggestions._deduplicate_contacts.return_value = []
            repository.apply_contact_scan_candidate.return_value = ContactScanStats(
                scanned_projects=1
            )
            self.assertEqual(
                self.service.rescan_customer_contacts(1).scanned_projects, 1
            )
        manager.close.assert_called()
        repository.close.assert_called()

        content = Mock()
        content.documents_for_folder.return_value = []
        content.has_pending_documents.return_value = True
        self.service._content_documents = content
        with (
            patch("app.services.customer_recognition.IndexManager", return_value=manager),
            patch("app.services.customer_recognition.CustomerRepository", return_value=repository),
        ):
            repository.get.return_value = Customer(id=1)
            repository.list_projects_for_customer.return_value = [project]
            with self.assertRaisesRegex(ValueError, "noch indexiert"):
                self.service.rescan_customer_contacts(1)

    def test_resolve_all_actions_and_invalid_assignments(self):
        repository = Mock()
        repository.apply_recognition_candidate.return_value = Customer(id=9)
        item = candidate(folder_paths=["/Service/2025/A", "/Other/B"])
        with patch("app.services.customer_recognition.CustomerRepository", return_value=repository):
            self.assertEqual(self.service.resolve_case(item, "ignore"), [])
            with self.assertRaisesRegex(ValueError, "Bestandskunden"):
                self.service.resolve_case(item, "assign")
            self.assertEqual(self.service.resolve_case(item, "assign", 4), [9])
            self.assertEqual(self.service.resolve_case(item, "together"), [9])
            repository.get_by_folder.side_effect = [Customer(id=3), None]
            self.assertEqual(self.service.resolve_case(item, "separate"), [9, 9])
            with self.assertRaisesRegex(ValueError, "Unbekannte"):
                self.service.resolve_case(item, "other")
        self.assertGreaterEqual(repository.close.call_count, 6)

    def test_merge_process_similar_owner_and_stored_decision_branches(self):
        reason = candidate(reason="manual")
        self.assertIs(self.service._merge_candidates([reason]), reason)
        merged = self.service._merge_candidates([
            candidate(
                city="", street="", postal_code="",
                contacts=[Contact(name="Max", email="x@example.test")],
            ),
            candidate(
                city="Berlin", street="Street", postal_code="1",
                contacts=[Contact(name="Max", email="x@example.test")],
                entity_type="Company", email="company@example.test",
            ),
        ])
        self.assertEqual(len(merged.contacts), 1)
        self.assertEqual(merged.street, "Street")

        repository = Mock()
        stats = RecognitionStats()
        pending = []
        repository.customer_ids_within_folder.side_effect = [[1], [2]]
        repository.get_by_folder.return_value = None
        self.assertFalse(self.service._process_candidate(repository, candidate(
            folder_paths=["a", "b"]
        ), stats, pending))
        repository.customer_ids_within_folder.side_effect = None
        repository.customer_ids_within_folder.return_value = [1]
        self.assertTrue(self.service._process_candidate(repository, candidate(), stats, pending))

        repository.customer_ids_within_folder.return_value = []
        repository.find_by_identity.return_value = [Customer(id=3)]
        self.assertTrue(self.service._process_candidate(repository, candidate(), stats, pending))
        repository.find_by_identity.return_value = []
        repository.find_by_name.return_value = [Customer(id=4)]
        self.assertTrue(self.service._process_candidate(repository, candidate(), stats, pending))
        repository.find_by_name.return_value = [Customer(id=4), Customer(id=None)]
        self.assertFalse(self.service._process_candidate(repository, candidate(), stats, pending))
        repository.find_by_name.return_value = []
        repository.list_customers.return_value = [Customer(id=5, display_name="Customer", city="")]
        self.assertFalse(self.service._process_candidate(repository, candidate(), stats, pending))
        repository.list_customers.return_value = []
        self.assertTrue(self.service._process_candidate(repository, candidate(), stats, pending))
        repository.list_customers.return_value = [
            Customer(display_name="Different", city="Paris"),
            Customer(display_name="Customer", city="Berlin"),
        ]
        self.assertEqual(len(self.service._similar_customers(repository, candidate())), 1)

        self.assertEqual(self.service._owner_ids_for_folder(repository, "folder"), [])
        repository.get_by_folder.return_value = Customer(id=8)
        self.assertEqual(self.service._owner_ids_for_folder(repository, "folder"), [8])

        for decision in (
            {"action": "ignore"}, {"action": "assign", "customer_id": 2},
            {"action": "unknown"},
        ):
            self.service._apply_stored_decision(
                repository, candidate(), decision, RecognitionStats()
            )
        repository.get_by_folder.side_effect = [None, Customer(id=2)]
        separate = candidate(folder_paths=["/Service/2026/A", "/fallback"])
        self.service._apply_stored_decision(
            repository, separate, {"action": "separate"}, RecognitionStats()
        )
        self.assertEqual(self.service._services_for_path("/Service/2026/A", ["fallback"]), ["Service"])
        self.assertEqual(self.service._services_for_path("/fallback", ["fallback"]), ["fallback"])
        self.assertEqual(self.service._years_for_path("/Service/2026/A", [1]), [2026])
        self.assertEqual(self.service._years_for_path("/fallback", [1]), [1])

    def test_synchronize_disabled_pending_changed_and_error_paths(self):
        disabled = CustomerRecognitionService(
            self.index_path, self.customer_path,
            CustomerRecognitionOptions(enabled=False),
        )
        self.assertEqual(disabled.synchronize().detected, 0)

        manager = Mock()
        repository = Mock()
        with (
            patch("app.services.customer_recognition.IndexManager", return_value=manager),
            patch("app.services.customer_recognition.CustomerRepository", return_value=repository),
            patch.object(self.service, "_load_candidates") as load,
        ):
            repository.get_recognition_decision.return_value = None
            load.return_value = [candidate(reason="ambiguous")]
            stats = self.service.synchronize()
            self.assertEqual(stats.pending, 1)

            changed = candidate()
            load.return_value = [changed]
            repository.has_previous_recognition_decision.return_value = True
            repository.customer_ids_within_folder.return_value = [3]
            repository.get_by_folder.return_value = None
            stats = self.service.synchronize()
            self.assertEqual(stats.pending, 1)
            self.assertIn("geändert", changed.reason)

            repository.record_extraction_observations.side_effect = RuntimeError("broken")
            with self.assertRaisesRegex(RuntimeError, "broken"):
                self.service.synchronize()
            self.assertEqual(repository.record_recognition_run.call_args.args[0].error, "broken")

    def test_load_candidates_keeps_explicit_reason_groups(self):
        manager = Mock()
        manager.list_project_roots.return_value = [{
            "year": 2026, "path": "/Service/2026/Customer",
            "customer_name": "Customer", "service_type": "Service",
        }]
        self.service._suggestions = Mock()
        self.service._suggestions.suggest_from_documents.return_value = CustomerSuggestion()
        explicit = candidate(reason="manual")
        with patch(
            "app.services.customer_recognition.RecognitionCandidate",
            return_value=explicit,
        ):
            result = self.service._load_candidates(manager, include_documents=False)
        self.assertEqual(result, [explicit])
