from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WINDOWS_POWERSHELL = shutil.which("powershell.exe") if sys.platform == "win32" else None


def run_windows_secret_functions(
    tmp_path: Path, assertions: str, *, python: Path | None = None
) -> None:
    """Exercise only the secret helpers, never the launcher's application startup."""
    harness = tmp_path / "secret helpers.ps1"
    harness.write_text(
        r"""
param([string]$LauncherPath, [string]$PythonPath, [string]$ConfigPath)
$ErrorActionPreference = 'Stop'
$WarningPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $LauncherPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) { throw 'Launcher contains PowerShell syntax errors.' }
$names = @('Protect-SecretFile', 'Write-SecretFile', 'Initialize-Secret')
$functions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -in $names
}, $false))
if ($functions.Count -ne $names.Count) { throw 'Secret helper definitions are missing.' }
foreach ($definition in $functions) {
    Invoke-Expression $definition.Extent.Text
}
$python = $PythonPath
$env:PAPAGUI_SERVER_CONFIG_PATH = $ConfigPath
$env:PAPAGUI_API_TOKEN = $null
New-Item -ItemType Directory -Path $ConfigPath | Out-Null
$secretPath = Join-Path $ConfigPath 'api-token'
try {
"""
        + assertions
        + r"""
} finally {
    if (Test-Path -LiteralPath $secretPath) {
        & icacls.exe $secretPath '/reset' *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Could not restore temporary file permissions.' }
    }
}
exit 0
""",
        encoding="utf-8-sig",  # Windows PowerShell 5.1 recognizes UTF-8 via its BOM.
    )
    result = subprocess.run(
        [
            WINDOWS_POWERSHELL or "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-LauncherPath",
            str(ROOT / "start.ps1"),
            "-PythonPath",
            str(python or sys.executable),
            "-ConfigPath",
            str(tmp_path / "synthetic config"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell is unavailable")
def test_windows_secret_generation_reuse_and_acl(tmp_path: Path) -> None:
    run_windows_secret_functions(
        tmp_path,
        r"""
Initialize-Secret 'PAPAGUI_API_TOKEN' 'api-token'
$initialToken = $env:PAPAGUI_API_TOKEN
if ($initialToken -notmatch '^[A-Za-z0-9_-]{43}$') {
    throw 'Generated token is missing or malformed.'
}
if ([IO.File]::ReadAllText($secretPath) -cne $initialToken) {
    throw 'Saved token does not match the client environment.'
}
$bytes = [IO.File]::ReadAllBytes($secretPath)
if ($bytes.Length -ne 43) { throw 'Saved token contains extra bytes.' }

$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = Get-Acl -LiteralPath $secretPath
if (-not $acl.AreAccessRulesProtected) { throw 'Secret still inherits permissions.' }
$rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
if ($rules.Count -eq 0) { throw 'Secret has no permissions for the current user.' }
$rights = 0
foreach ($rule in $rules) {
    if ($rule.IdentityReference.Value -ne $sid.Value -or $rule.IsInherited -or
        $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
        throw 'Secret permissions are not limited to the current user.'
    }
    $rights = $rights -bor $rule.FileSystemRights
}
$required = [System.Security.AccessControl.FileSystemRights]::Read -bor
    [System.Security.AccessControl.FileSystemRights]::Write
if (($rights -band $required) -ne $required) { throw 'Secret is not readable and writable.' }

$env:PAPAGUI_API_TOKEN = $null
$python = Join-Path $ConfigPath 'must-not-run.exe'
Initialize-Secret 'PAPAGUI_API_TOKEN' 'api-token'
if ($env:PAPAGUI_API_TOKEN -cne $initialToken -or
    [IO.File]::ReadAllText($secretPath) -cne $initialToken) {
    throw 'Existing token was not reused.'
}
""",
    )


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell is unavailable")
@pytest.mark.parametrize("output,exit_code", [("synthetic-token", 9), ("", 0)])
def test_windows_secret_generation_failure_does_not_write(
    tmp_path: Path, output: str, exit_code: int
) -> None:
    generator = tmp_path / "fake generator.cmd"
    generator.write_text(
        "@echo off\n" + (f"echo {output}\n" if output else "") + f"exit /b {exit_code}\n",
        encoding="ascii",
    )
    run_windows_secret_functions(
        tmp_path,
        r"""
$failed = $false
try {
    Initialize-Secret 'PAPAGUI_API_TOKEN' 'api-token'
} catch {
    if ($_.Exception.Message -ne 'Der API-Token konnte nicht erzeugt werden.') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Failed generation was accepted.' }
if (Test-Path -LiteralPath $secretPath) { throw 'Failed generation wrote a secret file.' }
if ($env:PAPAGUI_API_TOKEN) { throw 'Failed generation set the client token.' }
""",
        python=generator,
    )


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


def test_start_scripts_manage_only_the_client_token_secret() -> None:
    shell = (ROOT / "start.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
    for source in (shell, powershell):
        assert "PAPAGUI_ADMIN_PASSWORD" not in source
        assert "PAPAGUI_API_TOKEN_FILE" not in source  # Compose owns container paths.
    assert "umask 077" in shell
    assert 'chmod 600 "$secret_file"' in shell
    assert "icacls.exe" in powershell

    compose = yaml.safe_load(
        (ROOT / "deploy" / "server" / "compose.yaml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["papagui-server"]["environment"]
    assert environment["PAPAGUI_API_TOKEN_FILE"] == "/config/api-token"
    assert "PAPAGUI_API_TOKEN" not in environment
    assert not any("PASSWORD" in name for name in environment)


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
