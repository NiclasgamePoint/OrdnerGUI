from __future__ import annotations

from pathlib import Path
import io
import runpy
from types import SimpleNamespace

import pytest

import papagui_server.cli as cli


def _runtime_args(tmp_path: Path) -> list[str]:
    source = tmp_path / "source"
    source.mkdir(parents=True, exist_ok=True)
    return [
        "--source",
        str(source),
        "--data",
        str(tmp_path / "data"),
        "--config",
        str(tmp_path / "config"),
        "--source-id",
        "test-source",
        "--interval-seconds",
        "900",
    ]


def test_hash_password_interactive_prompt_and_short_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "prompted-password")
    assert cli.main(["hash-password"]) == 0
    assert capsys.readouterr().out.startswith("$argon2id$")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "short")
    with pytest.raises(ValueError, match="mindestens"):
        cli.main(["hash-password"])


def test_hash_password_stdin_avoids_argv_and_rejects_empty(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("stdin-password-123"))
    assert cli.main(["hash-password", "--stdin"]) == 0
    assert capsys.readouterr().out.startswith("$argon2id$")
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(""))
    with pytest.raises(ValueError, match="nicht leer"):
        cli.main(["hash-password", "--stdin"])
    with pytest.raises(SystemExit) as error:
        cli.main(["hash-password", "positional-password", "--stdin"])
    assert error.value.code == 2


def test_hash_password_rejects_argv_and_noninteractive_implicit_input(
    monkeypatch, capsys
) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["hash-password", "plaintext-secret"])
    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("not-read-implicitly"))
    with pytest.raises(ValueError, match="--stdin"):
        cli.main(["hash-password"])


def test_once_openapi_output_stdout_and_start_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PAPAGUI_ALLOW_INSECURE_NO_AUTH", "true")
    project = tmp_path / "source" / "Service" / "2026" / "CLI GmbH"
    project.mkdir(parents=True)
    (project / "file.txt").write_text("CLI content", encoding="utf-8")
    common = _runtime_args(tmp_path)
    assert cli.main(["once", *common, "--full-rebuild"]) == 0
    output = tmp_path / "openapi.json"
    assert cli.main(["openapi", *common, "--output", str(output)]) == 0
    assert '"openapi"' in output.read_text(encoding="utf-8")
    assert cli.main(["openapi", *common]) == 0
    assert '"PapaGUI Server API"' in capsys.readouterr().out

    monkeypatch.delenv("PAPAGUI_ALLOW_INSECURE_NO_AUTH")
    monkeypatch.delenv("PAPAGUI_API_TOKEN", raising=False)
    assert cli.main(["openapi", *_runtime_args(tmp_path / "invalid")]) == 2
    assert "konnte nicht gestartet" in capsys.readouterr().err


class FakeCoordinator:
    def __init__(self, restart: bool) -> None:
        self.restart_requested = restart

    def wait_for_restart(self, _timeout: float) -> bool:
        return self.restart_requested


class FakeServer:
    should_exit = False

    def __init__(self, _configuration) -> None:
        self.should_exit = False

    def run(self) -> None:
        self.should_exit = True


@pytest.mark.parametrize(("restart", "expected"), [(False, 0), (True, 75)])
def test_serve_and_default_command(
    tmp_path: Path, monkeypatch, restart: bool, expected: int
) -> None:
    container = SimpleNamespace(coordinator=FakeCoordinator(restart))
    monkeypatch.setattr(cli, "build_container", lambda _configuration: container)
    monkeypatch.setattr(cli, "create_app", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli.uvicorn, "Config", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli.uvicorn, "Server", FakeServer)
    monkeypatch.setenv("PAPAGUI_ALLOW_INSECURE_NO_AUTH", "on")
    arguments = [] if not restart else ["serve", *_runtime_args(tmp_path)]
    assert cli.main(arguments) == expected


def test_module_entrypoints_delegate_to_cli(monkeypatch) -> None:
    monkeypatch.setattr(cli, "main", lambda: 17)
    with pytest.raises(SystemExit) as error:
        runpy.run_module("papagui_server.__main__", run_name="__main__")
    assert error.value.code == 17
