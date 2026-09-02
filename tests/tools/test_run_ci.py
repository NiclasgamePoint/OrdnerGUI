from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
from PySide6.QtCore import QSettings

from tests import TEST_DIRECTORIES, TEST_ROOT, run_ci


def test_process_paths_and_qsettings_are_isolated() -> None:
    project_data = (Path(__file__).resolve().parents[2] / "data").resolve()

    assert Path(os.environ["PAPAGUI_DATA_DIR"]) != project_data
    assert Path(os.environ["PAPAGUI_DATA_DIR"]) == TEST_DIRECTORIES["data"]
    assert Path(os.environ["PAPAGUI_SOURCE_DIR"]) == TEST_DIRECTORIES["source"]
    assert Path(os.environ["PAPAGUI_SETTINGS_DIR"]) == TEST_DIRECTORIES["config"]
    assert QSettings.defaultFormat() == QSettings.Format.IniFormat

    settings = QSettings("PapaGUIHarness", "BootstrapTest")
    assert Path(settings.fileName()).resolve().is_relative_to(TEST_DIRECTORIES["config"])
    for directory in TEST_DIRECTORIES.values():
        assert directory.is_relative_to(TEST_ROOT)
        assert directory.is_dir()


def test_run_module_uses_private_environment_and_removes_it() -> None:
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
        assert test_root.is_dir()
        assert Path(environment["PAPAGUI_DATA_DIR"]).is_dir()
        assert Path(environment["PAPAGUI_SOURCE_DIR"]).is_dir()
        assert Path(environment["PAPAGUI_SETTINGS_DIR"]).is_dir()
        return subprocess.CompletedProcess(command, 0)

    with patch("tests.run_ci.subprocess.run", side_effect=completed_process):
        result = run_ci.run_module(
            "tests.tools.test_example",
            Path.cwd(),
            coverage=False,
            timeout_seconds=17.5,
        )

    assert result == 0
    assert captured["timeout"] == 17.5
    assert "pytest" in captured["command"]
    assert str(Path("tests/tools/test_example.py")) in captured["command"]
    assert not captured["test_root"].exists()
    environment = captured["environment"]
    assert Path(environment["PAPAGUI_DATA_DIR"]).parent == captured["test_root"]
    python_paths = environment["PYTHONPATH"].split(os.pathsep)
    assert str(Path.cwd() / "packages" / "contracts" / "src") in python_paths
    assert str(Path.cwd() / "packages" / "server" / "src") in python_paths
    assert str(Path.cwd() / "packages" / "client" / "src") in python_paths


def test_run_module_reports_timeout_and_cleans_environment() -> None:
    captured_root: Path | None = None

    def time_out(command, **kwargs):
        nonlocal captured_root
        captured_root = Path(kwargs["env"]["PAPAGUI_TEST_ROOT"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    stderr = io.StringIO()
    with (
        patch("tests.run_ci.subprocess.run", side_effect=time_out),
        patch("sys.stderr", stderr),
    ):
        result = run_ci.run_module(
            "tests.tools.test_slow",
            Path.cwd(),
            coverage=False,
            timeout_seconds=0.25,
        )

    assert result == run_ci.MODULE_TIMEOUT_EXIT_CODE
    assert captured_root is not None
    assert not captured_root.exists()
    assert "tests.tools.test_slow" in stderr.getvalue()
    assert "0.25 seconds" in stderr.getvalue()
    assert "terminated" in stderr.getvalue()


def test_main_rejects_non_positive_module_timeout(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        run_ci.main(["--module-timeout", "0"])

    assert raised.value.code == 2
    assert "must be greater than zero" in capsys.readouterr().err
