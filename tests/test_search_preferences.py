import unittest

from PySide6.QtCore import QSettings

from app.core.config import SETTINGS_APP, SETTINGS_ORG
from app.core.search_models import SearchPreferences, SearchSort


class SearchPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.previous = {
            key: self.settings.value(key) if self.settings.contains(key) else None
            for key in (
                SearchPreferences.SORT_KEY,
                SearchPreferences.SUBFOLDERS_KEY,
            )
        }
        self.settings.remove(SearchPreferences.SORT_KEY)
        self.settings.remove(SearchPreferences.SUBFOLDERS_KEY)

    def tearDown(self):
        for key, value in self.previous.items():
            if value is None:
                self.settings.remove(key)
            else:
                self.settings.setValue(key, value)

    def test_defaults_and_persisted_search_options(self):
        preferences = SearchPreferences()
        self.assertEqual(preferences.load(), (SearchSort.RELEVANCE, False))

        preferences.save(SearchSort.DATE, True)

        self.assertEqual(
            SearchPreferences().load(),
            (SearchSort.DATE, True),
        )


if __name__ == "__main__":
    unittest.main()
