from __future__ import annotations

from enum import Enum
import unittest

from papagui_contracts import (
    ApiError,
    Capabilities,
    ConflictPayload,
    Contact,
    ContractValidationError,
    Customer,
    CustomerJournalEntry,
    ErrorDetail,
    GenerationComponentKind,
    GenerationComponentManifest,
    GenerationManifest,
    IdempotencyKey,
    IndexProgress,
    IndexRunState,
    IndexSettings,
    IndexStatus,
    MutationEnvelope,
    MutationOperation,
    MutationTarget,
    ServerState,
    ServerStatus,
    SystemInfo,
)
from papagui_contracts._base import (
    JsonDto,
    encode_json_value,
    mapping_get,
    optional_int,
    optional_string,
    require_bool,
    require_int,
    require_mapping,
    require_string,
    string_tuple,
)


SHA = "C" * 64
KEY = "edge-request-123"


def component(
    kind: GenerationComponentKind = GenerationComponentKind.INDEX,
    **changes: object,
) -> GenerationComponentManifest:
    values: dict[str, object] = {
        "kind": kind,
        "generation": "generation-1",
        "created_at": "2026-09-02T10:00:00+00:00",
        "archive": "/v2/generations/index/generation-1/archive",
        "size": 42,
        "sha256": SHA,
    }
    values.update(changes)
    return GenerationComponentManifest(**values)  # type: ignore[arg-type]


class BaseValidationEdgeTests(unittest.TestCase):
    def test_json_base_rejects_invalid_json_and_non_objects(self):
        for value in ("not json", "[]"):
            with self.subTest(value=value):
                with self.assertRaises(ContractValidationError):
                    Contact.from_json(value)
        with self.assertRaises(NotImplementedError):
            JsonDto.from_dict({})

    def test_json_encoder_covers_supported_containers_and_rejects_objects(self):
        class Value(str, Enum):
            ITEM = "item"

        self.assertEqual(encode_json_value(Value.ITEM), "item")
        self.assertEqual(encode_json_value({"x": (1, True)}), {"x": [1, True]})
        self.assertEqual(sorted(encode_json_value({"a", "b"})), ["a", "b"])
        with self.assertRaises(TypeError):
            encode_json_value(object())

    def test_primitive_validators_cover_success_defaults_and_failures(self):
        self.assertEqual(require_mapping({}, "value"), {})
        self.assertEqual(require_string("x", "value"), "x")
        self.assertIsNone(optional_string(None, "value"))
        self.assertEqual(optional_string("x", "value"), "x")
        self.assertTrue(require_bool(True, "value"))
        self.assertEqual(require_int(2.0, "value"), 2)
        self.assertIsNone(optional_int(None, "value"))
        self.assertEqual(optional_int(1, "value"), 1)
        self.assertEqual(string_tuple(None, "value"), ())
        self.assertEqual(mapping_get({"x": 1}, "x", 2), 1)
        self.assertEqual(mapping_get({}, "x", 2), 2)

        failing_calls = (
            lambda: require_mapping([], "value"),
            lambda: require_string(1, "value"),
            lambda: require_string("", "value"),
            lambda: require_bool(1, "value"),
            lambda: require_int(True, "value"),
            lambda: require_int(1.5, "value"),
            lambda: require_int(0, "value", minimum=1),
            lambda: require_int(2, "value", maximum=1),
            lambda: string_tuple("x", "value"),
        )
        for call in failing_calls:
            with self.subTest(call=call):
                with self.assertRaises(ContractValidationError):
                    call()


class CustomerValidationEdgeTests(unittest.TestCase):
    def test_customer_payload_array_validation_and_idempotency_helpers(self):
        self.assertEqual(Customer.from_dict({"contacts": None}).contacts, ())
        self.assertEqual(Customer.from_dict({"journal": None}).journal_entries, ())
        with self.assertRaises(ContractValidationError):
            Customer.from_dict({"contacts": "invalid"})
        with self.assertRaises(ContractValidationError):
            Customer(journal_entries=(object(),))  # type: ignore[arg-type]

        key = IdempotencyKey(KEY)
        self.assertEqual(str(key), KEY)
        self.assertEqual(key.to_dict(), {"value": KEY})
        self.assertIs(IdempotencyKey.from_value(key), key)
        self.assertEqual(IdempotencyKey.from_value({"value": KEY}), key)
        self.assertEqual(IdempotencyKey.from_dict({"value": KEY}), key)

    def test_direct_string_enums_and_key_are_normalized(self):
        envelope = MutationEnvelope(
            operation="create",  # type: ignore[arg-type]
            target="customer",  # type: ignore[arg-type]
            idempotency_key=KEY,  # type: ignore[arg-type]
            payload=Customer(display_name="Neu"),
        )
        self.assertIs(envelope.operation, MutationOperation.CREATE)
        self.assertIs(envelope.target, MutationTarget.CUSTOMER)
        self.assertEqual(envelope.idempotency_key, IdempotencyKey(KEY))

        for changes in (
            {"operation": "unknown", "target": "customer"},
            {"operation": "create", "target": "unknown"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ContractValidationError):
                    MutationEnvelope(
                        operation=changes["operation"],  # type: ignore[arg-type]
                        target=changes["target"],  # type: ignore[arg-type]
                        idempotency_key=KEY,  # type: ignore[arg-type]
                        payload=Customer(),
                    )

    def test_mutation_missing_ids_wrong_payload_and_id_mismatch_fail(self):
        with self.assertRaises(ContractValidationError):
            MutationEnvelope(
                MutationOperation.UPDATE,
                MutationTarget.CUSTOMER,
                IdempotencyKey(KEY),
                expected_revision=1,
                payload=Customer(id=1),
            )
        with self.assertRaises(ContractValidationError):
            MutationEnvelope(
                MutationOperation.CREATE,
                MutationTarget.CUSTOMER,
                IdempotencyKey(KEY),
                payload=CustomerJournalEntry(),
            )
        with self.assertRaises(ContractValidationError):
            MutationEnvelope(
                MutationOperation.UPDATE,
                MutationTarget.CUSTOMER,
                IdempotencyKey(KEY),
                expected_revision=1,
                target_id=2,
                payload=Customer(id=1),
            )

    def test_mutation_parser_rejects_unknown_values_and_parses_journal_alias(self):
        for payload in (
            {"operation": "unknown", "idempotency_key": KEY},
            {
                "operation": "create",
                "target": "unknown",
                "idempotency_key": KEY,
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ContractValidationError):
                    MutationEnvelope.from_dict(payload)

        parsed = MutationEnvelope.from_dict(
            {
                "operation": "create",
                "target": "journal",
                "idempotency_key": KEY,
                "customer_id": 1,
                "journal_entry": {"body": "Notiz"},
            }
        )
        self.assertEqual(parsed.payload, CustomerJournalEntry(body="Notiz"))


class ErrorValidationEdgeTests(unittest.TestCase):
    def test_error_detail_defaults_and_nested_api_error(self):
        self.assertEqual(
            ErrorDetail.from_dict({}),
            ErrorDetail(code="invalid", message="Invalid value"),
        )
        nested = ApiError.from_dict(
            {
                "error": {
                    "code": "bad",
                    "message": "Bad request",
                    "status": 400,
                    "details": [],
                }
            }
        )
        self.assertEqual(nested.status, 400)
        with self.assertRaises(ContractValidationError):
            ApiError(code="bad", message="Bad", status=400, details=(object(),))  # type: ignore[arg-type]
        with self.assertRaises(ContractValidationError):
            ApiError.from_dict({"details": "invalid"})

    def test_conflict_constructor_validation_and_string_normalization(self):
        error = ApiError(code="conflict", message="Conflict", status=409)
        normalized = ConflictPayload(
            error=error,
            target="customer",  # type: ignore[arg-type]
            expected_revision=1,
            idempotency_key=KEY,  # type: ignore[arg-type]
        )
        self.assertIs(normalized.target, MutationTarget.CUSTOMER)
        self.assertEqual(normalized.idempotency_key, IdempotencyKey(KEY))

        invalid_values = (
            {"error": object(), "target": MutationTarget.CUSTOMER},
            {"error": error, "target": "unknown"},
            {"error": error, "target": MutationTarget.CUSTOMER, "current": object()},
            {
                "error": error,
                "target": MutationTarget.CUSTOMER,
                "current": Customer(revision=2),
                "actual_revision": 3,
            },
        )
        for values in invalid_values:
            values.setdefault("expected_revision", 1)
            with self.subTest(values=values):
                with self.assertRaises(ContractValidationError):
                    ConflictPayload(**values)  # type: ignore[arg-type]

    def test_conflict_parser_rejects_wrong_status_and_target(self):
        for payload in (
            {
                "error": {"code": "bad", "message": "Bad", "status": 400},
                "expected_revision": 1,
            },
            {
                "error": "Conflict",
                "target": "unknown",
                "expected_revision": 1,
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ContractValidationError):
                    ConflictPayload.from_dict(payload)


class GenerationValidationEdgeTests(unittest.TestCase):
    def test_component_normalizes_kind_hash_and_server_local_archive(self):
        parsed = component(kind="index", sha256=SHA)  # type: ignore[arg-type]
        self.assertIs(parsed.kind, GenerationComponentKind.INDEX)
        self.assertEqual(parsed.sha256, SHA.lower())
        restored = GenerationComponentManifest.from_dict(parsed.to_dict())
        self.assertEqual(restored, parsed)

    def test_invalid_component_identifiers_and_kinds_fail(self):
        invalid_changes = (
            {"kind": "unknown"},
            {"generation": "../generation"},
            {"archive": "https://evil.test/archive.zip"},
            {"archive": "//evil.test/archive.zip"},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(ContractValidationError):
                    component(**changes)
        with self.assertRaises(ContractValidationError):
            GenerationComponentManifest.from_dict(
                {
                    "kind": "unknown",
                    "generation": "one",
                    "created_at": "now",
                    "archive": "one.zip",
                    "size": 1,
                    "sha256": SHA,
                }
            )

    def test_manifest_validates_component_roles_and_schema_flags(self):
        index = component()
        customers = component(
            GenerationComponentKind.CUSTOMERS,
            archive="customers.zip",
        )
        cases = (
            {"index": object(), "customers": customers},
            {"index": index, "customers": object()},
            {"index": customers, "customers": customers},
            {"index": index, "customers": index},
            {
                "index": index,
                "customers": customers,
                "schema_version": 1,
                "legacy_combined": False,
            },
            {
                "index": index,
                "customers": customers,
                "schema_version": 1,
                "legacy_combined": True,
            },
            {
                "index": index,
                "customers": customers,
                "schema_version": 2,
                "legacy_combined": True,
            },
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ContractValidationError):
                    GenerationManifest(created_at="now", **values)  # type: ignore[arg-type]

    def test_manifest_customer_alias_fallback_and_component_enum_input(self):
        customers = component(
            GenerationComponentKind.CUSTOMERS,
            archive="customers.zip",
        )
        parsed = GenerationManifest.from_dict(
            {
                "schema_version": 2,
                "created_at": "now",
                "components": {"index": None, "customer": customers.to_dict()},
            }
        )
        self.assertIs(parsed.component(GenerationComponentKind.CUSTOMERS), parsed.customers)
        with self.assertRaises(ValueError):
            parsed.component("unknown")


class StatusAndSystemValidationEdgeTests(unittest.TestCase):
    def test_status_aliases_fraction_cap_and_direct_normalization(self):
        self.assertIs(ServerState.parse(ServerState.ONLINE), ServerState.ONLINE)
        self.assertIs(IndexRunState.parse(IndexRunState.RUNNING), IndexRunState.RUNNING)
        self.assertIs(IndexRunState.parse("ok"), IndexRunState.COMPLETED)
        self.assertEqual(IndexProgress(processed_items=3, total_items=2).fraction, 1.0)
        status = IndexStatus(state="running")  # type: ignore[arg-type]
        server = ServerStatus(state="online", index=status)  # type: ignore[arg-type]
        self.assertTrue(status.is_active)
        self.assertIs(server.state, ServerState.ONLINE)

    def test_invalid_status_dto_members_fail(self):
        with self.assertRaises(ContractValidationError):
            IndexProgress(current_source=object())  # type: ignore[arg-type]
        with self.assertRaises(ContractValidationError):
            IndexStatus(progress=object())  # type: ignore[arg-type]
        with self.assertRaises(ContractValidationError):
            ServerStatus(state=ServerState.ONLINE, index=object())  # type: ignore[arg-type]

    def test_system_and_capability_members_are_validated(self):
        with self.assertRaises(ContractValidationError):
            Capabilities(generation_schema_versions="2")  # type: ignore[arg-type]
        with self.assertRaises(ContractValidationError):
            Capabilities(generation_schema_versions=())
        with self.assertRaises(ContractValidationError):
            Capabilities.from_dict({"generation_schema_versions": "2"})
        with self.assertRaises(ContractValidationError):
            SystemInfo(server_version="1", capabilities=object())  # type: ignore[arg-type]

    def test_settings_accepts_direct_string_profile_and_rejects_invalid_bool(self):
        settings = IndexSettings(resource_profile="balanced")  # type: ignore[arg-type]
        self.assertEqual(settings.resource_profile.value, "balanced")
        with self.assertRaises(ContractValidationError):
            IndexSettings(resource_profile="unknown")  # type: ignore[arg-type]
        with self.assertRaises(ContractValidationError):
            IndexSettings.from_dict({"ocr_enabled": "yes"})


if __name__ == "__main__":
    unittest.main()
