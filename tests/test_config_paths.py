from __future__ import annotations

import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class ConfigPathTests(unittest.TestCase):
    def test_default_base_dir_is_derived_from_file_location(self):
        from app.core import config

        expected = Path(config.__file__).resolve().parents[2]
        self.assertEqual(config.BASE_DIR, expected)

    def test_environment_overrides_are_applied(self):
        module_name = "app.core.config"
        old_values = {
            "PAPAGUI_BASE_DIR": os.environ.get("PAPAGUI_BASE_DIR"),
            "PAPAGUI_DATA_DIR": os.environ.get("PAPAGUI_DATA_DIR"),
            "PAPAGUI_SOURCE_DIR": os.environ.get("PAPAGUI_SOURCE_DIR"),
        }
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                os.environ["PAPAGUI_BASE_DIR"] = str(root / "base")
                os.environ["PAPAGUI_DATA_DIR"] = str(root / "data")
                os.environ["PAPAGUI_SOURCE_DIR"] = str(root / "source")
                module = importlib.import_module(module_name)
                module = importlib.reload(module)

                self.assertEqual(module.BASE_DIR, (root / "base").resolve())
                self.assertEqual(module.DATA_DIR, (root / "data").resolve())
                self.assertEqual(module.BAUVORHABEN_DIR, (root / "source").resolve())
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            module = importlib.import_module(module_name)
            importlib.reload(module)

    def test_customer_recognition_defaults_to_enabled_without_saved_value(self):
        from app.core import config

        class SettingsWithoutSavedValue:
            def value(self, _key, default=None):
                return default

        with patch.object(
            config, "QSettings", return_value=SettingsWithoutSavedValue()
        ):
            self.assertTrue(config.load_customer_recognition_options().enabled)

    def test_customer_recognition_preserves_saved_disabled_value(self):
        from app.core import config

        class SettingsWithDisabledRecognition:
            def value(self, key, default=None):
                if key == "customer_recognition/enabled":
                    return False
                return default

        with patch.object(
            config, "QSettings", return_value=SettingsWithDisabledRecognition()
        ):
            self.assertFalse(config.load_customer_recognition_options().enabled)


if __name__ == "__main__":
    unittest.main()
