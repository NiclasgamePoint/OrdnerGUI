from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.customer_suggestion import CustomerSuggestionService


class CustomerSuggestionTests(unittest.TestCase):
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
        self.assertEqual(suggestion.contacts, [])

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
            self.assertEqual(suggestion.email, "max.mueller@example.de")
            normalized_street = suggestion.street.casefold().replace("ß", "ss")
            self.assertIn("strasse", normalized_street)
            self.assertGreaterEqual(len(suggestion.contacts), 1)
            self.assertTrue(any(contact.name for contact in suggestion.contacts))

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
