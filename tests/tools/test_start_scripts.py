from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is unavailable")
def test_posix_launchers_have_valid_bash_syntax() -> None:
    subprocess.run(
        [
            shutil.which("bash") or "bash",
            "-n",
            "start.sh",
            "start.command",
            "index-tray.sh",
            "tools/docker_smoke.sh",
        ],
        cwd=ROOT,
        check=True,
    )


def test_launchers_delegate_to_the_platform_native_entrypoint() -> None:
    command = (ROOT / "start.command").read_text(encoding="utf-8")
    batch = (ROOT / "start.bat").read_text(encoding="utf-8")
    assert 'exec ./start.sh "$@"' in command
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass" in batch
    assert '"%~dp0start.ps1" %*' in batch


def test_start_scripts_keep_secrets_out_of_child_argv_and_docker_environment() -> None:
    shell = (ROOT / "start.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
    for source in (shell, powershell):
        assert "hash-password --stdin" in source
        assert "hash_admin_password(sys.argv[1])" not in source
        assert "PAPAGUI_API_TOKEN_FILE" not in source  # Compose owns container paths.
    assert "umask 077" in shell
    assert 'chmod 600 "$secret_file"' in shell
    assert "RedirectStandardInput = $true" in powershell
    assert "icacls.exe" in powershell

    compose = yaml.safe_load(
        (ROOT / "deploy" / "server" / "compose.yaml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["papagui-server"]["environment"]
    assert environment["PAPAGUI_API_TOKEN_FILE"] == "/config/api-token"
    assert environment["PAPAGUI_ADMIN_PASSWORD_HASH_FILE"] == ("/config/admin-password-hash")
    assert "PAPAGUI_API_TOKEN" not in environment
    assert "PAPAGUI_ADMIN_PASSWORD_HASH" not in environment


def test_windows_health_wait_detects_a_crashed_detached_container() -> None:
    powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
    assert "$dockerStart.Refresh()" in powershell
    assert "compose -f deploy/server/compose.yaml ps --status running --quiet" in powershell
    assert "Der Servercontainer ist nach dem Compose-Start nicht mehr aktiv" in powershell


def test_compose_defaults_to_loopback_and_keeps_source_read_only() -> None:
    for relative in ("compose.yaml", "deploy/server/compose.yaml"):
        compose = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        service = compose["services"]["papagui-server"]
        assert service["ports"] == [
            "${PAPAGUI_API_BIND_ADDRESS:-127.0.0.1}:${PAPAGUI_API_PORT:-8765}:8765"
        ]
        source = next(volume for volume in service["volumes"] if volume["target"] == "/source")
        assert source["read_only"] is True
