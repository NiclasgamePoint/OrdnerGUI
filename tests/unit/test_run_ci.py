from __future__ import annotations

import io
import os
import subprocess
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings

from tests import TEST_DIRECTORIES, TEST_ROOT
from tests import run_ci


class TestEnvironmentBootstrapTests(unittest.TestCase):
    def test_process_paths_and_qsettings_are_isolated(self):
        project_data = (Path(__file__).resolve().parents[2] / "data").resolve()

        self.assertNotEqual(Path(os.environ["PAPAGUI_DATA_DIR"]), project_data)
        self.assertEqual(Path(os.environ["PAPAGUI_DATA_DIR"]), TEST_DIRECTORIES["data"])
        self.assertEqual(
            Path(os.environ["PAPAGUI_SOURCE_DIR"]),
            TEST_DIRECTORIES["source"],
        )
        self.assertEqual(
            Path(os.environ["PAPAGUI_SETTINGS_DIR"]),
            TEST_DIRECTORIES["config"],
        )
        self.assertEqual(QSettings.defaultFormat(), QSettings.Format.IniFormat)

        settings = QSettings("PapaGUIHarness", "BootstrapTest")
        settings_path = Path(settings.fileName()).resolve()
        self.assertTrue(settings_path.is_relative_to(TEST_DIRECTORIES["config"]))

        for directory in TEST_DIRECTORIES.values():
            self.assertTrue(directory.is_relative_to(TEST_ROOT))
            self.assertTrue(directory.is_dir())


class RunCiTests(unittest.TestCase):
    def test_run_module_passes_a_private_environment_and_cleans_it(self):
        captured: dict[str, object] = {}

        def completed_process(command, **kwargs):
            environment = kwargs["env"]
            test_root = Path(environment["PAPAGUI_TEST_ROOT"])
            captured.update(
                command=command,
                environment=environment,
                test_root=test_root,
                timeout=kwargs["timeout"],
            )
            self.assertTrue(test_root.is_dir())
            self.assertTrue(Path(environment["PAPAGUI_DATA_DIR"]).is_dir())
            self.assertTrue(Path(environment["PAPAGUI_SOURCE_DIR"]).is_dir())
            self.assertTrue(Path(environment["PAPAGUI_SETTINGS_DIR"]).is_dir())
            return subprocess.CompletedProcess(command, 0)

        with patch("tests.run_ci.subprocess.run", side_effect=completed_process):
            result = run_ci.run_module(
                "tests.unit.test_example",
                Path.cwd(),
                coverage=False,
                timeout_seconds=17.5,
            )

        self.assertEqual(result, 0)
        self.assertEqual(captured["timeout"], 17.5)
        self.assertIn("unittest", captured["command"])
        self.assertFalse(captured["test_root"].exists())
        environment = captured["environment"]
        self.assertEqual(
            Path(environment["PAPAGUI_DATA_DIR"]).parent,
            captured["test_root"],
        )

    def test_run_module_reports_timeout_and_cleans_its_environment(self):
        captured_root: Path | None = None

        def time_out(command, **kwargs):
            nonlocal captured_root
            captured_root = Path(kwargs["env"]["PAPAGUI_TEST_ROOT"])
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        stderr = io.StringIO()
        with (
            patch("tests.run_ci.subprocess.run", side_effect=time_out),
            redirect_stderr(stderr),
        ):
            result = run_ci.run_module(
                "tests.unit.test_slow",
                Path.cwd(),
                coverage=False,
                timeout_seconds=0.25,
            )

        self.assertEqual(result, run_ci.MODULE_TIMEOUT_EXIT_CODE)
        self.assertIsNotNone(captured_root)
        self.assertFalse(captured_root.exists())
        self.assertIn("tests.unit.test_slow", stderr.getvalue())
        self.assertIn("0.25 seconds", stderr.getvalue())
        self.assertIn("terminated", stderr.getvalue())

    def test_main_rejects_non_positive_module_timeout(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            run_ci.main(["--module-timeout", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("must be greater than zero", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
