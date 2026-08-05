from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.core.folder_structure import FolderStructureClassifier


class FolderStructureTests(unittest.TestCase):
    def test_classifies_only_customer_roots_from_2016_onwards(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Bauvorhaben"
            classifier = FolderStructureClassifier()

            project = root / "DEKRA" / "2016" / "Müller, Berlin"
            nested = project / "Bilder" / "Innen"
            result = classifier.classify(nested, root)

            self.assertIsNotNone(result)
            self.assertEqual(result.path, str(project))
            self.assertEqual(result.customer_name, "Müller")
            self.assertEqual(result.city, "Berlin")
            self.assertEqual(result.service_type, "DEKRA")
            self.assertEqual(result.year, 2016)
            self.assertIsNone(
                classifier.classify(root / "DEKRA" / "2015" / "Alt, Köln", root)
            )
            self.assertIsNone(
                classifier.classify(root / "DEKRA" / "Vorlagen" / "Muster", root)
            )
            self.assertIsNone(classifier.classify(root / "DEKRA" / "2026", root))


if __name__ == "__main__":
    unittest.main()
