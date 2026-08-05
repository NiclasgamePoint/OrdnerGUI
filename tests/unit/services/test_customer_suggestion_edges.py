from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.core.customer_recognition_models import ExtractionEvidence
from app.services.customer_suggestion import CustomerSuggestion, CustomerSuggestionService
from tests.base.test_case import PapaGuiTestCase


class CustomerSuggestionEdgeTests(PapaGuiTestCase):
    def setUp(self):
        super().setUp()
        self.service = CustomerSuggestionService()

    @staticmethod
    def evidence(confidence=0.9, **changes):
        values = {
            "field_name": "contact_name", "value": "Max Muster",
            "normalized_value": "max muster", "source_path": "/a",
            "excerpt": "", "position": 0, "rule": "Kundenname aus Dokumentfeld",
            "confidence": confidence,
        }
        values.update(changes)
        return ExtractionEvidence(**values)

    def test_collect_documents_skips_nonfiles_types_empty_and_stops_at_limit(self):
        edges = self.temp_path / "edges"
        edges.mkdir()
        (edges / "directory.txt").mkdir()
        (edges / "ignored.bin").write_bytes(b"x")
        (edges / "empty.txt").touch()
        self.assertEqual(self.service._collect_documents(edges), [])
        bulk = self.temp_path / "bulk"
        bulk.mkdir()
        for number in range(25):
            (bulk / f"{number:02}.txt").write_text("x", encoding="utf-8")
        documents = self.service._collect_documents(bulk)
        self.assertEqual(len(documents), 24)

    def test_private_evidence_name_and_path_rejection_edges(self):
        self.assertFalse(self.service._has_automatic_evidence([]))
        self.assertTrue(self.service._has_automatic_evidence([self.evidence()]))
        self.assertFalse(CustomerSuggestion().has_values())
        self.assertTrue(CustomerSuggestion(display_name="Name").has_values())
        weak = self.evidence(0.7)
        self.service._mark_automatic_evidence([weak])
        self.assertFalse(weak.automatic)

        for display, entity in (
            ("", "Privatperson"), ("A" * 61, "Privatperson"),
            ("Company GmbH", "Privatperson"), ("Group Verein", "Privatperson"),
            ("Max 2", "Privatperson"), ("one two three four five", "Privatperson"),
            ("lower case", "Privatperson"), ("Max Muster", "Unternehmen"),
        ):
            self.assertEqual(
                self.service._path_contact_fallback(self.temp_path, display, entity), ""
            )
        self.assertEqual(
            self.service._path_contact_fallback(
                self.temp_path, "Max Muster", "Privatperson"
            ),
            "Max Muster",
        )

        self.assertEqual(self.service._name_before_address(["Auftragnehmer", "Max Muster"], 2), "")
        self.assertEqual(
            self.service._name_before_address(["noise@example.test", "Max Muster"], 2),
            "Max Muster",
        )
        self.assertEqual(
            self.service._name_before_address(
                ["Max Muster", "noise@example.test"], 2
            ),
            "Max Muster",
        )
        for value in (
            "", "x" * 61, "mail@example.test", "Datum 01.01.2026",
            "Max 2 Muster", "Single", "Muster GmbH", "A B",
        ):
            self.assertEqual(self.service._clean_name_candidate(value), "")

    def test_extract_file_text_all_formats_and_failures(self):
        text = self.temp_path / "text.txt"
        text.write_text("value", encoding="utf-8")
        self.assertEqual(self.service._extract_file_text(text, "txt"), "value")
        pages = [SimpleNamespace(extract_text=lambda: "pdf"), SimpleNamespace(extract_text=lambda: None)]
        with patch("app.services.customer_suggestion.PdfReader", return_value=SimpleNamespace(pages=pages)):
            self.assertEqual(self.service._extract_file_text(text, "pdf"), "pdf\n")
        document = SimpleNamespace(paragraphs=[SimpleNamespace(text="docx"), SimpleNamespace(text="")])
        with patch("app.services.customer_suggestion.Document", return_value=document):
            self.assertEqual(self.service._extract_file_text(text, "docx"), "docx")
        self.service._converter = Mock()
        self.service._converter.extract_legacy_doc.return_value = "doc"
        self.assertEqual(self.service._extract_file_text(text, "doc"), "doc")
        self.assertEqual(self.service._extract_file_text(text, "unknown"), "")
        with patch("app.services.customer_suggestion.Path.read_text", side_effect=OSError):
            self.assertEqual(self.service._extract_file_text(text, "txt"), "")

    def test_entity_first_names_and_extraction_unusual_lines(self):
        with patch("app.services.customer_suggestion.load_first_names", return_value=set()):
            self.assertFalse(self.service._firstname_in_customer_name_evidence([self.evidence()]))
        with patch("app.services.customer_suggestion.load_first_names", return_value={"max"}):
            self.assertTrue(self.service._firstname_in_customer_name_evidence([self.evidence()]))

        document = {
            "path": "/a.txt", "content": (
                "Auftragnehmer\nKunde: GmbH\nAuftragnehmer\nMax Muster\n"
                "Empfänger\nMax Muster\nMusterstraße 1\n12345 Berlin"
            ),
        }
        evidence, _contacts = self.service._extract_document_evidence(
            document, False, "Muster"
        )
        self.assertTrue(any(item.field_name == "street" for item in evidence))

        late_lines = ["Zeile"] * 41 + ["Musterstraße 1", "12345 Berlin"]
        evidence, _contacts = self.service._extract_document_evidence(
            {"path": "/late", "content": "\n".join(late_lines)}, False
        )
        self.assertTrue(any(item.field_name == "street" for item in evidence))

    def test_path_extraction_without_city_and_empty_service(self):
        suggestion = CustomerSuggestion()
        self.service._extract_from_path(
            self.temp_path / "" / "2026" / "Project", "Suggested", suggestion
        )
        self.assertEqual(suggestion.display_name, "Suggested")
        empty_service = CustomerSuggestion()
        fake_path = SimpleNamespace(name="Project", parts=("", "2026", "Project"))
        self.service._extract_from_path(fake_path, "Suggested", empty_service)
        self.assertEqual(empty_service.service_types, [])

    def test_preferred_automatic_name_does_not_duplicate_fallback_contacts(self):
        preferred = {
            "path": "/Angebot.txt", "filename": "Angebot.txt",
            "file_type": "txt", "content": "Ansprechpartner: Max Muster",
        }
        fallback = {
            "path": "/Other.txt", "filename": "Other.txt",
            "file_type": "txt", "content": "Ansprechpartner: Other Person",
        }
        suggestion = self.service.suggest_from_documents(
            self.temp_path / "Project", "Project", [preferred, fallback], ["angebot"]
        )
        self.assertTrue(any(contact.name == "Max Muster" for contact in suggestion.contacts))
