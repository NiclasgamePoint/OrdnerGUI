from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.customer_suggestion import CustomerSuggestionService


class CustomerSuggestionTests(unittest.TestCase):
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
            self.assertIn("Blower Door", suggestion.service_types)
            self.assertEqual(suggestion.city, "Beispielstadt")
            self.assertEqual(suggestion.postal_code, "22844")
            self.assertEqual(suggestion.email, "max.mueller@example.de")
            normalized_street = suggestion.street.casefold().replace("ß", "ss")
            self.assertIn("strasse", normalized_street)
            self.assertGreaterEqual(len(suggestion.contacts), 1)
            self.assertTrue(any(contact.name for contact in suggestion.contacts))


if __name__ == "__main__":
    unittest.main()
