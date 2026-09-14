from __future__ import annotations

import json
import os
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
        env={key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"},
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

# An existing explicit grant must also be removed, not merely inherited ACEs.
& icacls.exe $secretPath '/grant' '*S-1-1-0:R' *> $null
if ($LASTEXITCODE -ne 0) { throw 'Could not create synthetic explicit ACE.' }
Protect-SecretFile $secretPath

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


@pytest.mark.parametrize("script", ["start.sh", "start.command", "index-tray.sh", "tools/docker_smoke.sh"])
def test_posix_launchers_have_valid_bash_syntax(script: str) -> None:
    bash = shutil.which("bash")
    if sys.platform == "win32":
        # The Windows system32 bash.exe is a WSL launcher, not a native shell.
        git = shutil.which("git")
        bundled = Path(git).parent.parent / "bin/bash.exe" if git else None
        bash = str(bundled) if bundled is not None and bundled.is_file() else None
    if bash is None:
        pytest.skip("native bash is unavailable")
    subprocess.run(
        [
            bash,
            "-n",
            script,
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


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell is unavailable")
@pytest.mark.parametrize(
    "scenario,expected_commands,health_calls,sleeps,message",
    [
        ("create", ["info", "version", "up"], 1, 0, "PapaGUI-Server ist erreichbar"),
        ("retry", ["info", "version", "up", "ps"], 2, 1, "PapaGUI-Server ist erreichbar"),
        ("build_failure", ["info", "version", "up"], 0, 0, "Exitcode 7"),
        ("engine_offline", ["info"], 0, 0, "Docker ist nicht erreichbar"),
        ("no_compose", ["info", "version"], 0, 0, "Docker Compose ist nicht verfuegbar"),
        ("crash", ["info", "version", "up", "ps"], 1, 0, "nicht mehr aktiv"),
        ("timeout", ["info", "version", "up", "ps", "ps", "ps"], 3, 3, "Zeitlimit"),
        ("skip", [], 0, 0, ""),
        ("missing_source", [], 0, 0, "Quellordner"),
        ("missing_docker", [], 0, 0, "Docker wurde nicht gefunden"),
    ],
)
def test_windows_server_bootstrap(
    tmp_path: Path,
    scenario: str,
    expected_commands: list[str],
    health_calls: int,
    sleeps: int,
    message: str,
) -> None:
    """Run the real bootstrap with a native fake Docker, no GUI or customer data."""
    fake_bin = tmp_path / "fake docker bin"
    fake_bin.mkdir()
    (fake_bin / "docker.cmd").write_text(
        "@echo off\n"
        'echo %*>>"%~dp0calls.txt"\n'
        f'if "%~1"=="info" exit /b {9 if scenario == "engine_offline" else 0}\n'
        f'if "%~2"=="version" exit /b {8 if scenario == "no_compose" else 0}\n'
        'if "%~4"=="ps" (\n'
        + ("  rem No running container\n" if scenario == "crash" else "  echo synthetic-container\n")
        + "  exit /b 0\n)\n"
        'if not "%~4"=="up" exit /b 99\n'
        "echo synthetic Docker build progress 1>&2\n"
        f"exit /b {7 if scenario == 'build_failure' else 0}\n",
        encoding="ascii",
    )
    harness = tmp_path / "server bootstrap.ps1"
    harness.write_text(
        r"""
param([string]$LauncherPath, [string]$FakeBin, [string]$Scenario)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $LauncherPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) { throw 'Launcher contains PowerShell syntax errors.' }
$definition = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Start-LocalServer'
}, $false)
if (-not $definition) { throw 'Server bootstrap function is missing.' }
Invoke-Expression $definition.Extent.Text
$env:PATH = $FakeBin + ';' + $env:PATH
if ((Get-Command docker).Source -ne (Join-Path $FakeBin 'docker.cmd')) {
    throw 'Fake Docker must take precedence over the real installation.'
}
$env:PAPAGUI_SKIP_DOCKER = if ($Scenario -eq 'skip') { '1' } else { $null }
$env:PAPAGUI_SOURCE_PATH = Join-Path $PSScriptRoot 'synthetic source'
$env:PAPAGUI_INDEX_SERVER_URL = 'http://synthetic.invalid:8765/'
if ($Scenario -ne 'missing_source') {
    New-Item -ItemType Directory -Path $env:PAPAGUI_SOURCE_PATH | Out-Null
}
if ($Scenario -eq 'missing_docker') {
    function Get-Command { param($Name, $ErrorAction) return $null }
}
$script:healthCalls = 0
$script:sleeps = 0
function Invoke-RestMethod {
    [CmdletBinding()]
    param([string]$Uri, [int]$TimeoutSec)
    if ($Uri -ne 'http://synthetic.invalid:8765/health' -or $TimeoutSec -ne 1) {
        throw 'Unexpected healthcheck request.'
    }
    $script:healthCalls++
    if ($Scenario -in @('crash', 'timeout') -or
        ($Scenario -eq 'retry' -and $script:healthCalls -eq 1)) {
        throw 'Synthetic server is not ready.'
    }
    return @{status = 'ok'}
}
function Start-Sleep { param([int]$Seconds) $script:sleeps++ }
Start-LocalServer -HealthCheckAttempts 3
if ($ErrorActionPreference -ne 'Stop') { throw 'Bootstrap changed caller error handling.' }
Write-Output ('STATE:' + (@{health_calls = $script:healthCalls; sleeps = $script:sleeps} |
    ConvertTo-Json -Compress))
exit 0
""",
        encoding="utf-8-sig",
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
            "-FakeBin",
            str(fake_bin),
            "-Scenario",
            scenario,
        ],
        cwd=tmp_path,
        # Actions runs Python under PowerShell 7; its module paths cannot be
        # imported by the Windows PowerShell 5.1 executable used by start.bat.
        env={key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"},
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert message in output
    state = json.loads(next(line[6:] for line in result.stdout.splitlines() if line.startswith("STATE:")))
    assert state == {"health_calls": health_calls, "sleeps": sleeps}
    command_log = fake_bin / "calls.txt"
    commands = command_log.read_text().splitlines() if command_log.exists() else []
    arguments = {
        "info": "info",
        "version": "compose version",
        "up": "compose -f deploy/server/compose.yaml up -d --build papagui-server",
        "ps": "compose -f deploy/server/compose.yaml ps --status running --quiet papagui-server",
    }
    assert commands == [arguments[command] for command in expected_commands]
    if "up" in expected_commands:
        assert "synthetic Docker build progress" in output  # Native stderr is normal progress.


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell is unavailable")
def test_windows_desktop_bootstrap_starts_only_windowed_client(tmp_path: Path) -> None:
    harness = tmp_path / "desktop bootstrap.ps1"
    harness.write_text(
        r"""
param([string]$LauncherPath)
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $LauncherPath, [ref]$tokens, [ref]$parseErrors
)
$definition = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Start-DesktopClient'
}, $false)
if (-not $definition) { throw 'Desktop launcher is missing.' }
Invoke-Expression $definition.Extent.Text
$python = Join-Path $PSScriptRoot 'python.exe'
New-Item -Path (Join-Path $PSScriptRoot 'pythonw.exe') -ItemType File | Out-Null
$script:launches = @()
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle)
    $script:launches += @{file = $FilePath; arguments = $ArgumentList; style = $WindowStyle}
}
Start-DesktopClient --offline
if ($script:launches.Count -ne 1) { throw 'Desktop bootstrap must launch exactly one process.' }
$launch = $script:launches[0]
if ($launch.file -ne (Join-Path $PSScriptRoot 'pythonw.exe') -or
    ($launch.arguments -join ' ') -ne '-m papagui_client --offline' -or
    $launch.style -ne 'Hidden') {
    throw 'Desktop was not launched without a console or arguments were lost.'
}
exit 0
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            WINDOWS_POWERSHELL or "powershell.exe", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(harness),
            "-LauncherPath", str(ROOT / "start.ps1"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell is unavailable")
def test_windows_start_retains_nas_settings_and_clears_only_generated_overrides(tmp_path):
    """Exercise complete startup twice; substitute only Docker and the GUI launch."""
    shutil.copy(ROOT / "start.ps1", tmp_path / "start.ps1")
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools/prepare_client_start.py", tmp_path / "tools/prepare_client_start.py")
    (tmp_path / "inspect_client.py").write_text(
        "import os\n"
        "from dataclasses import replace\n"
        "from pathlib import Path\n"
        "from papagui_client.adapters.json_config import JsonClientConfigRepository\n"
        "root = Path(os.environ['PAPAGUI_CLIENT_DATA_ROOT'])\n"
        "repo = JsonClientConfigRepository(root / 'client-config.json')\n"
        "config = repo.resolve()\n"
        "assert config.sources['server_url'] == 'persisted'\n"
        "assert config.sources['api_token'] == 'persisted'\n"
        "assert config.sources['source_mappings'] == 'persisted'\n"
        "assert config.sources['theme'] == 'environment:PAPAGUI_CLIENT_THEME'\n"
        "if not Path('first-start-ok').exists():\n"
        "    assert config.settings.server_url == 'http://127.0.0.1:8765'\n"
        "    token_file = Path(os.environ['PAPAGUI_SERVER_CONFIG_PATH']) / 'api-token'\n"
        "    assert token_file.read_text() == config.settings.api_token\n"
        "    repo.save(replace(config.settings, server_url='https://nas.test', api_token='synthetic-nas'))\n"
        "    Path('first-start-ok').touch()\n"
        "else:\n"
        "    assert config.settings.server_url == 'https://nas.test'\n"
        "    assert config.settings.api_token == 'synthetic-nas'\n"
        "    Path('second-start-ok').touch()\n",
        encoding="utf-8",
    )
    harness = tmp_path / "launch test.ps1"
    harness.write_text(
        r"""
param([string]$PythonPath)
$tokens = $null
$parseErrors = $null
$path = Join-Path $PSScriptRoot 'start.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $path, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count) { throw 'Launcher syntax error.' }
$source = [IO.File]::ReadAllText($path)
$source = $source.Replace("Join-Path `$PSScriptRoot '.venv/Scripts/python.exe'", '$PythonPath')
$definitions = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -in @('Start-LocalServer', 'Start-DesktopClient')
}, $false)
foreach ($definition in $definitions) {
    $replacement = if ($definition.Name -eq 'Start-LocalServer') {
        "function Start-LocalServer { Add-Content -LiteralPath 'docker-calls.txt' -Value 'start' }"
    } else {
        'function Start-DesktopClient { & $python inspect_client.py; if ($LASTEXITCODE) { throw ''Client assertions failed.'' } }'
    }
    $source = $source.Replace($definition.Extent.Text, $replacement)
}
$testRoot = $PSScriptRoot
$source = $source.Replace('$PSScriptRoot', '$testRoot')
Invoke-Expression $source
""",
        encoding="utf-8-sig",
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PAPAGUI_")}
    environment["PYTHONPATH"] = os.pathsep.join(
        str(ROOT / "packages" / package / "src") for package in ("client", "contracts")
    )
    environment["PAPAGUI_CLIENT_THEME"] = "dark"  # Intentional override must survive.
    arguments = [WINDOWS_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                 "Bypass", "-File", str(harness), "-PythonPath", sys.executable]
    for _ in range(2):
        result = subprocess.run(arguments, cwd=tmp_path, env=environment,
                                capture_output=True, text=True, errors="replace", timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "first-start-ok").exists()
    assert (tmp_path / "second-start-ok").exists()
    assert (tmp_path / "docker-calls.txt").read_text().splitlines() == ["start"]


def test_compose_defaults_to_loopback_and_keeps_source_read_only() -> None:
    for relative in ("compose.yaml", "deploy/server/compose.yaml"):
        compose = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
        service = compose["services"]["papagui-server"]
        assert service["ports"] == [
            "${PAPAGUI_API_BIND_ADDRESS:-127.0.0.1}:${PAPAGUI_API_PORT:-8765}:8765"
        ]
        source = next(volume for volume in service["volumes"] if volume["target"] == "/source")
        assert source["read_only"] is True
