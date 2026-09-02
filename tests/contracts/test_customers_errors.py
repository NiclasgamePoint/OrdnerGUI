from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from papagui_contracts import (
    ApiError,
    ConflictPayload,
    Contact,
    ContractValidationError,
    Customer,
    CustomerJournalEntry,
    ErrorDetail,
    IdempotencyKey,
    MutationEnvelope,
    MutationOperation,
    MutationTarget,
)


KEY = "request-12345678"


def customer(*, revision: int = 3) -> Customer:
    return Customer(
        id=12,
        revision=revision,
        folder_path="2026/Kunde C",
        folder_paths=("2026/Kunde C", "2025/Kunde C"),
        display_name="Kunde C",
        entity_type="Unternehmen",
        service_types=("Planung",),
        company="Kunde C GmbH",
        email="mail@example.test",
        phone="+49 123",
        street="Musterweg 1",
        postal_code="12345",
        city="Berlin",
        contacts=(Contact(name="Ada", role="Planung"),),
        journal_entries=(
            CustomerJournalEntry(
                id=5,
                customer_id=12,
                entry_number=1,
                title="Anruf",
                revision=revision,
            ),
        ),
        notes=("Notiz",),
        tags=("aktiv",),
    )


class CustomerContractTests(unittest.TestCase):
    def test_customer_roundtrip_matches_existing_v1_shape_and_is_deeply_immutable(self):
        original = customer()

        restored = Customer.from_json(original.to_json())

        self.assertEqual(restored, original)
        self.assertIsInstance(restored.contacts, tuple)
        self.assertIsInstance(restored.folder_paths, tuple)
        self.assertIsInstance(restored.journal_entries, tuple)
        self.assertEqual(restored.contacts[0].name, "Ada")
        with self.assertRaises(FrozenInstanceError):
            restored.display_name = "Anders"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            restored.tags.append("neu")  # type: ignore[attr-defined]

    def test_unknown_v1_customer_fields_are_ignored(self):
        parsed = Customer.from_dict(
            {
                "id": 2,
                "revision": 4,
                "display_name": "Altbestand",
                "contacts": [{"name": "Grace", "future": "ignored"}],
                "future_server_field": "ignored",
            }
        )

        self.assertEqual(parsed.id, 2)
        self.assertEqual(parsed.revision, 4)
        self.assertEqual(parsed.contacts, (Contact(name="Grace"),))

    def test_contact_and_journal_entry_roundtrip(self):
        contact = Contact(name="Lin", email="lin@example.test")
        entry = CustomerJournalEntry(
            id=3,
            customer_id=12,
            entry_number=2,
            title="Anruf",
            body="Rückruf vereinbart",
            revision=5,
        )

        self.assertEqual(Contact.from_json(contact.to_json()), contact)
        self.assertEqual(CustomerJournalEntry.from_json(entry.to_json()), entry)

    def test_invalid_identifiers_and_revisions_fail(self):
        with self.assertRaises(ContractValidationError):
            Customer(id=0)
        with self.assertRaises(ContractValidationError):
            Customer(revision=-1)
        with self.assertRaises(ContractValidationError):
            Customer(contacts=(object(),))  # type: ignore[arg-type]


class MutationContractTests(unittest.TestCase):
    def test_update_envelope_roundtrip_carries_revision_and_idempotency_key(self):
        original = MutationEnvelope(
            operation=MutationOperation.UPDATE,
            target=MutationTarget.CUSTOMER,
            idempotency_key=IdempotencyKey(KEY),
            expected_revision=3,
            target_id=12,
            payload=customer(),
        )

        wire = original.to_dict()
        restored = MutationEnvelope.from_json(original.to_json())

        self.assertEqual(wire["idempotency_key"], KEY)
        self.assertEqual(wire["expected_revision"], 3)
        self.assertEqual(restored, original)

    def test_customer_create_and_journal_delete_shapes_are_supported(self):
        create = MutationEnvelope(
            operation=MutationOperation.CREATE,
            target=MutationTarget.CUSTOMER,
            idempotency_key=IdempotencyKey.new(),
            payload=Customer(display_name="Neu"),
        )
        delete = MutationEnvelope(
            operation=MutationOperation.DELETE,
            target=MutationTarget.JOURNAL,
            idempotency_key=IdempotencyKey(KEY),
            expected_revision=7,
            target_id=9,
            customer_id=12,
        )

        self.assertEqual(MutationEnvelope.from_json(create.to_json()), create)
        self.assertEqual(MutationEnvelope.from_json(delete.to_json()), delete)

    def test_invalid_mutation_combinations_are_rejected(self):
        cases = (
            {
                "operation": MutationOperation.UPDATE,
                "target": MutationTarget.CUSTOMER,
                "idempotency_key": IdempotencyKey(KEY),
                "target_id": 12,
                "payload": customer(),
            },
            {
                "operation": MutationOperation.DELETE,
                "target": MutationTarget.CUSTOMER,
                "idempotency_key": IdempotencyKey(KEY),
                "expected_revision": 3,
                "target_id": 12,
                "payload": customer(),
            },
            {
                "operation": MutationOperation.CREATE,
                "target": MutationTarget.JOURNAL,
                "idempotency_key": IdempotencyKey(KEY),
                "payload": CustomerJournalEntry(body="Text"),
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ContractValidationError):
                    MutationEnvelope(**values)  # type: ignore[arg-type]

        with self.assertRaises(ContractValidationError):
            IdempotencyKey("short")


class ErrorContractTests(unittest.TestCase):
    def test_structured_api_error_roundtrip(self):
        original = ApiError(
            code="validation_failed",
            message="Daten sind ungültig",
            status=422,
            details=(ErrorDetail(code="required", message="Name fehlt", field="display_name"),),
            request_id="req-1",
        )

        self.assertEqual(ApiError.from_json(original.to_json()), original)

    def test_v1_error_string_is_normalized(self):
        parsed = ApiError.from_dict(
            {"error": "Nicht gefunden", "status": 404, "request_id": "req-old"}
        )

        self.assertEqual(parsed.code, "legacy_error")
        self.assertEqual(parsed.message, "Nicht gefunden")
        self.assertEqual(parsed.status, 404)

    def test_conflict_payload_roundtrip_and_v1_current_revision_inference(self):
        original = ConflictPayload(
            error=ApiError(
                code="revision_conflict",
                message="Kunde wurde bereits geändert",
                status=409,
            ),
            target=MutationTarget.CUSTOMER,
            expected_revision=3,
            actual_revision=4,
            current=customer(revision=4),
            idempotency_key=IdempotencyKey(KEY),
        )

        self.assertEqual(ConflictPayload.from_json(original.to_json()), original)

        legacy = ConflictPayload.from_dict(
            {
                "error": "Konflikt",
                "expected_revision": 3,
                "current": customer(revision=4).to_dict(),
            }
        )
        self.assertEqual(legacy.error.status, 409)
        self.assertEqual(legacy.actual_revision, 4)
        self.assertEqual(legacy.current, customer(revision=4))

    def test_non_conflict_error_is_rejected_in_conflict_payload(self):
        with self.assertRaises(ContractValidationError):
            ConflictPayload(
                error=ApiError(code="bad", message="bad", status=400),
                target=MutationTarget.CUSTOMER,
                expected_revision=1,
            )


if __name__ == "__main__":
    unittest.main()
