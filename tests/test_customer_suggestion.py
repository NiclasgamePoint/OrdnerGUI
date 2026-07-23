from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.customer_models import Contact
from app.core.customer_recognition_models import ExtractionEvidence
from app.services.customer_suggestion import (
    CustomerSuggestion,
    CustomerSuggestionService,
)


class CustomerSuggestionTests(unittest.TestCase):
    def test_address_fields_from_different_blocks_are_not_combined(self):
        service = CustomerSuggestionService()
        suggestion = CustomerSuggestion()
        suggestion.evidence = [
            ExtractionEvidence(
                field_name="street",
                value="Musterstraße 10",
                normalized_value="musterstraße 10",
                source_path="a.pdf",
                excerpt="Straße",
                position=3,
                rule="vollständiger Adressblock",
                confidence=0.95,
                automatic=True,
            ),
            ExtractionEvidence(
                field_name="postal_code",
                value="12345",
                normalized_value="12345",
                source_path="b.pdf",
                excerpt="PLZ Ort",
                position=8,
                rule="vollständiger Adressblock",
                confidence=0.95,
                automatic=True,
            ),
            ExtractionEvidence(
                field_name="city",
                value="Musterstadt",
                normalized_value="musterstadt",
                source_path="b.pdf",
                excerpt="PLZ Ort",
                position=8,
                rule="vollständiger Adressblock",
                confidence=0.95,
                automatic=True,
            ),
        ]

        service._apply_resolved_evidence(suggestion)

        self.assertEqual(suggestion.street, "")
        self.assertEqual(suggestion.postal_code, "")
        self.assertEqual(suggestion.city, "")

    def test_formal_salutations_are_strong_name_evidence(self):
        service = CustomerSuggestionService()
        for line, expected in (
            ("Sehr geehrter Herr Max Müller,", "Max Müller"),
            ("Sehr geehrte Frau Dr. Erika Muster,", "Erika Muster"),
        ):
            with self.subTest(line=line):
                suggestion = service.suggest_from_documents(
                    Path("/tmp/Muster GmbH, Berlin"),
                    "Muster GmbH",
                    [{
                        "path": "/tmp/Anschreiben.pdf",
                        "filename": "Anschreiben.pdf",
                        "file_type": "pdf",
                        "content": line,
                    }],
                )
                match = next(
                    item for item in suggestion.evidence
                    if item.field_name == "contact_name" and item.value == expected
                )
                self.assertTrue(match.automatic)
                self.assertGreaterEqual(match.confidence, 0.90)
                self.assertIn(Contact(name=expected), suggestion.contacts)

    def test_contract_customer_roles_exclude_contractor(self):
        service = CustomerSuggestionService()
        suggestion = service.suggest_from_documents(
            Path("/tmp/Muster GmbH, Berlin"),
            "Muster GmbH",
            [{
                "path": "/tmp/Vertrag.pdf",
                "filename": "Vertrag.pdf",
                "file_type": "pdf",
                "content": (
                    "Auftraggeber:\nHerr Max Müller\n"
                    "Auftragnehmer:\nHerr Falscher Absender"
                ),
            }],
        )

        self.assertIn(Contact(name="Max Müller"), suggestion.contacts)
        self.assertFalse(any(
            contact.name == "Falscher Absender" for contact in suggestion.contacts
        ))

    def test_early_address_name_is_weak_until_folder_surname_matches(self):
        service = CustomerSuggestionService()
        document = {
            "path": "/tmp/Brief.pdf",
            "filename": "Brief.pdf",
            "file_type": "pdf",
            "content": "Max Müller\nMusterstraße 12\n12345 Berlin",
        }

        weak = service.suggest_from_documents(
            Path("/tmp/Projekt GmbH, Berlin"), "Projekt GmbH", [document]
        )
        matched = service.suggest_from_documents(
            Path("/tmp/Müller, Berlin"), "Müller", [document]
        )

        weak_name = next(
            item for item in weak.evidence
            if item.field_name == "contact_name" and item.value == "Max Müller"
        )
        matched_name = next(
            item for item in matched.evidence
            if item.field_name == "contact_name" and item.value == "Max Müller"
        )
        self.assertEqual(weak_name.confidence, 0.88)
        self.assertFalse(weak_name.automatic)
        self.assertEqual(matched_name.confidence, 0.94)
        self.assertTrue(matched_name.automatic)

    def test_two_address_blocks_confirm_name_and_sender_block_is_ignored(self):
        service = CustomerSuggestionService()
        documents = [
            {
                "path": f"/tmp/Brief-{number}.pdf",
                "filename": f"Brief-{number}.pdf",
                "file_type": "pdf",
                "content": "Max Müller\nMusterstraße 12\n12345 Berlin",
            }
            for number in (1, 2)
        ]
        suggestion = service.suggest_from_documents(
            Path("/tmp/Projekt GmbH, Berlin"), "Projekt GmbH", documents
        )
        sender = service.suggest_from_documents(
            Path("/tmp/Projekt GmbH, Berlin"),
            "Projekt GmbH",
            [{
                "path": "/tmp/Briefkopf.pdf",
                "filename": "Briefkopf.pdf",
                "file_type": "pdf",
                "content": (
                    "Absender: Max Absender\nEigenweg 1\n12345 Berlin"
                ),
            }],
        )

        self.assertIn(Contact(name="Max Müller"), suggestion.contacts)
        self.assertFalse(any(
            item.field_name == "contact_name" and item.value == "Max Absender"
            for item in sender.evidence
        ))

    def test_safe_preferred_address_does_not_stop_name_fallback(self):
        service = CustomerSuggestionService()
        suggestion = service.suggest_from_documents(
            Path("/tmp/Muster GmbH, Berlin"),
            "Muster GmbH",
            [
                {
                    "path": "/tmp/Angebot.pdf",
                    "filename": "Angebot.pdf",
                    "file_type": "pdf",
                    "content": (
                        "Kunde: Muster GmbH\nMusterstraße 12\n12345 Berlin"
                    ),
                },
                {
                    "path": "/tmp/Notiz.pdf",
                    "filename": "Notiz.pdf",
                    "file_type": "pdf",
                    "content": "Ansprechpartnerin: Erika Muster",
                },
            ],
        )

        self.assertIn(Contact(name="Erika Muster"), suggestion.contacts)

    def test_strong_contact_name_is_kept_without_email_or_phone(self):
        service = CustomerSuggestionService()
        suggestion = service.suggest_from_documents(
            Path("/tmp/Muster GmbH, Berlin"),
            "Muster GmbH",
            [{
                "path": "/tmp/Anschreiben.pdf",
                "filename": "Anschreiben.pdf",
                "file_type": "pdf",
                "content": "Ansprechpartnerin: Erika Muster",
            }],
        )

        self.assertEqual(suggestion.contacts, [Contact(name="Erika Muster")])
        self.assertTrue(any(
            item.field_name == "contact_name"
            and item.value == "Erika Muster"
            and item.automatic
            for item in suggestion.evidence
        ))

    def test_folder_names_are_not_invented_as_contact_people(self):
        service = CustomerSuggestionService()

        person = service.suggest_from_documents(
            Path("/tmp/Müller, Berlin"), "Müller", []
        )
        company = service.suggest_from_documents(
            Path("/tmp/Muster GmbH, Berlin"), "Muster GmbH", []
        )
        surname_with_legal_form_letters = service.suggest_from_documents(
            Path("/tmp/Wagner, Berlin"), "Wagner", []
        )

        self.assertEqual(person.contacts, [])
        self.assertEqual(company.contacts, [])
        self.assertEqual(
            surname_with_legal_form_letters.contacts,
            [],
        )
        self.assertEqual(person.entity_type, "Unternehmen")

    def test_two_documents_confirm_a_weaker_contact_name(self):
        evidence = [
            ExtractionEvidence(
                field_name="contact_name",
                value="Max Müller",
                normalized_value="max müller",
                source_path=f"/tmp/bericht-{number}.pdf",
                excerpt="Max Müller",
                position=1,
                rule="plausibler Name",
                confidence=0.80,
            )
            for number in (1, 2)
        ]

        CustomerSuggestionService._mark_automatic_evidence(evidence)

        self.assertTrue(all(item.automatic for item in evidence))

    def test_phone_validation_accepts_real_numbers_and_rejects_noise(self):
        service = CustomerSuggestionService()

        self.assertEqual(
            service._clean_phone_candidate(
                "+44 20 7946 0958", "Telefon: +44 20 7946 0958"
            ),
            "+44 20 7946 0958",
        )
        self.assertEqual(
            service._clean_phone_candidate(
                "+49 40 1234567", "Telefon: +49 40 1234567"
            ),
            "+49 40 1234567",
        )
        self.assertEqual(
            service._clean_phone_candidate(
                "+49 40 1234567", "Fax: +49 40 1234567"
            ),
            "",
        )
        self.assertEqual(
            service._clean_phone_candidate("05.03.2024", "Datum 05.03.2024"),
            "",
        )

    def test_labeled_and_salutation_names_are_linked_to_nearby_contact_data(self):
        service = CustomerSuggestionService()
        documents = [{
            "path": "/tmp/Anschreiben.pdf",
            "filename": "Anschreiben.pdf",
            "file_type": "pdf",
            "content": (
                "Ansprechpartnerin: Erika Muster\n"
                "erika.muster@example.de\n"
                "Mobil: 0176 12345678"
            ),
        }]

        suggestion = service.suggest_from_documents(
            Path("/tmp/Muster, Berlin"), "Muster", documents
        )

        self.assertTrue(any(
            contact.name == "Erika Muster"
            and contact.email == "erika.muster@example.de"
            and contact.phone == "0176 12345678"
            for contact in suggestion.contacts
        ))

    def test_two_independent_documents_confirm_weak_email(self):
        service = CustomerSuggestionService()
        documents = [
            {
                "path": f"/tmp/bericht-{number}.pdf",
                "filename": f"bericht-{number}.pdf",
                "file_type": "pdf",
                "content": "max.mustermann@example.de",
            }
            for number in (1, 2)
        ]

        suggestion = service.suggest_from_documents(
            Path("/tmp/Mustermann, Berlin"), "Mustermann", documents
        )

        self.assertEqual(suggestion.email, "max.mustermann@example.de")
        self.assertTrue(any(
            item.field_name == "email" and item.automatic
            for item in suggestion.evidence
        ))

    def test_conflicting_weak_emails_are_not_applied(self):
        service = CustomerSuggestionService()
        documents = [
            {
                "path": f"/tmp/{name}.pdf",
                "filename": f"{name}.pdf",
                "file_type": "pdf",
                "content": email,
            }
            for name, email in (
                ("bericht", "eins@example.de"),
                ("protokoll", "zwei@example.de"),
            )
        ]

        suggestion = service.suggest_from_documents(
            Path("/tmp/Mustermann, Berlin"), "Mustermann", documents
        )

        self.assertEqual(suggestion.email, "")
        self.assertTrue(suggestion.evidence)
        self.assertFalse(any(
            item.field_name == "email" and item.automatic
            for item in suggestion.evidence
        ))

    def test_spreadsheet_and_signature_contacts_are_ignored(self):
        service = CustomerSuggestionService()
        documents = [
            {
                "path": "/tmp/kontakte.xlsx",
                "filename": "kontakte.xlsx",
                "file_type": "xlsx",
                "content": "Frau Falsche Person falsch@example.de +49 40 1234567",
            },
            {
                "path": "/tmp/Angebot.pdf",
                "filename": "Angebot.pdf",
                "file_type": "pdf",
                "content": "Mit freundlichen Grüßen\nFrau Eigene Person\neigen@example.de",
            },
        ]

        suggestion = service.suggest_from_documents(
            Path("/tmp/Mustermann, Berlin"), "Mustermann", documents
        )

        self.assertEqual(suggestion.email, "")
        self.assertEqual(
            suggestion.contacts,
            [Contact(name="Mustermann")],
        )

    def test_suggests_customer_data_from_path_and_text_document(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory) / "Blower Door" / "2016" / "Müller, Beispielstadt"
            folder.mkdir(parents=True)
            (folder / "Angebot_xyz.txt").write_text(
                """
                Angebot an: Max Müller
                Musterstraße 12
                22844 Beispielstadt
                E-Mail: max.mueller@example.de
                Telefon: +49 40 1234567
                """.strip(),
                encoding="utf-8",
            )

            service = CustomerSuggestionService()
            suggestion = service.suggest_for_folder(folder)

            self.assertEqual(suggestion.display_name, "Müller")
            self.assertEqual(suggestion.entity_type, "Privatperson")
            self.assertIn("Blower Door", suggestion.service_types)
            self.assertEqual(suggestion.city, "Beispielstadt")
            self.assertEqual(suggestion.postal_code, "22844")
            self.assertEqual(suggestion.email, "")
            normalized_street = suggestion.street.casefold().replace("ß", "ss")
            self.assertIn("strasse", normalized_street)
            self.assertGreaterEqual(len(suggestion.contacts), 1)
            self.assertTrue(any(
                contact.name == "Max Müller"
                and contact.email == "max.mueller@example.de"
                and contact.phone == "+49 40 1234567"
                for contact in suggestion.contacts
            ))

    def test_two_company_contacts_keep_role_email_and_phone_separate(self):
        service = CustomerSuggestionService()
        suggestion = service.suggest_from_documents(
            Path("/tmp/Architekturbüro Mayer GmbH, Hamburg"),
            "Architekturbüro Mayer GmbH",
            [{
                "path": "/tmp/Ansprechpartner.txt",
                "filename": "Ansprechpartner.txt",
                "file_type": "txt",
                "content": (
                    "Ansprechpartnerin: Frau Maike Mayer\n"
                    "Architektin\nmaike@mayer.de\nTelefon: 040 111111\n\n"
                    "Ansprechpartner: Herr Max Mustermann\n"
                    "Projektleiter\nmax@mayer.de\nTelefon: 040 222222"
                ),
            }],
        )

        self.assertIn(
            Contact("Maike Mayer", "Architektin", "maike@mayer.de", "040 111111"),
            suggestion.contacts,
        )
        self.assertIn(
            Contact(
                "Max Mustermann", "Projektleiter",
                "max@mayer.de", "040 222222",
            ),
            suggestion.contacts,
        )

    def test_infers_organization_and_ignores_noise_contacts(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory) / "Blower Door" / "2024" / "Kita Lentföhrden, Lingststädt"
            folder.mkdir(parents=True)
            (folder / "bericht.txt").write_text(
                """
                Brh= 0,00m
                IBAN DE12 3456 7890 0053107 07956
                Datum 05.03.2024
                E-Mail: info@example.de
                Telefon: 08268 171 91193
                Ansprechpartnerin:
                Erika Muster
                erika.muster@example.de
                Mobil: 0176 12345678
                """.strip(),
                encoding="utf-8",
            )

            service = CustomerSuggestionService()
            suggestion = service.suggest_for_folder(folder)

            self.assertEqual(suggestion.entity_type, "Organisation")
            self.assertEqual(suggestion.phone, "08268 17191193")
            self.assertTrue(all("Brh" not in contact.name for contact in suggestion.contacts))
            self.assertTrue(all("0053107" not in contact.phone for contact in suggestion.contacts))
            self.assertTrue(any(
                contact.name == "Erika Muster"
                and contact.email == "erika.muster@example.de"
                and contact.phone == "0176 12345678"
                for contact in suggestion.contacts
            ))


if __name__ == "__main__":
    unittest.main()
