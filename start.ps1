$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) {
    $python = Join-Path $PSScriptRoot 'venv/Scripts/python.exe'
}
if (-not (Test-Path $python)) {
    throw 'Keine virtuelle Umgebung gefunden (.venv oder venv).'
}

$packagePaths = @(
    (Join-Path $PSScriptRoot 'packages/contracts/src'),
    (Join-Path $PSScriptRoot 'packages/server/src'),
    (Join-Path $PSScriptRoot 'packages/client/src')
)
$env:PYTHONPATH = ($packagePaths + @($env:PYTHONPATH) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
# Distinguish convenience defaults from intentional environment overrides.
$clientDefaultVariables = @('PAPAGUI_INDEX_SERVER_URL', 'PAPAGUI_API_TOKEN', 'PAPAGUI_SOURCE_MAPPINGS') |
    Where-Object { -not [Environment]::GetEnvironmentVariable($_) }
$prepareArguments = @()
foreach ($variable in $clientDefaultVariables) {
    $prepareArguments += @('--default-variable', $variable)
}
$localApiPort = if ($env:PAPAGUI_API_PORT) { $env:PAPAGUI_API_PORT } else { '8765' }
$env:PAPAGUI_INDEX_SERVER_URL = if ($env:PAPAGUI_INDEX_SERVER_URL) { $env:PAPAGUI_INDEX_SERVER_URL } else { "http://127.0.0.1:$localApiPort" }
$env:PAPAGUI_CLIENT_DATA_ROOT = if ($env:PAPAGUI_CLIENT_DATA_ROOT) { $env:PAPAGUI_CLIENT_DATA_ROOT } else { Join-Path $PSScriptRoot 'client-data' }
$env:PAPAGUI_SERVER_DATA_PATH = if ($env:PAPAGUI_SERVER_DATA_PATH) { $env:PAPAGUI_SERVER_DATA_PATH } else { Join-Path $PSScriptRoot 'docker-server-data' }
$env:PAPAGUI_SERVER_CONFIG_PATH = if ($env:PAPAGUI_SERVER_CONFIG_PATH) { $env:PAPAGUI_SERVER_CONFIG_PATH } else { Join-Path $PSScriptRoot 'docker-config' }
$env:PAPAGUI_SOURCE_ID = if ($env:PAPAGUI_SOURCE_ID) { $env:PAPAGUI_SOURCE_ID } else { 'primary' }
New-Item -ItemType Directory -Force $env:PAPAGUI_CLIENT_DATA_ROOT, $env:PAPAGUI_SERVER_DATA_PATH, $env:PAPAGUI_SERVER_CONFIG_PATH | Out-Null

function Protect-SecretFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        # /inheritance:r /grant:r leaves unrelated explicit ACEs in place.
        # Replace the DACL atomically, including on GitHub's Windows image.
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($existingRule in @($acl.GetAccessRules(
            $true, $false, [System.Security.Principal.SecurityIdentifier]
        ))) {
            $acl.RemoveAccessRuleSpecific($existingRule)
        }
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $identity, 'FullControl', 'Allow'
        )
        $acl.AddAccessRule($rule)
        if ($PSVersionTable.PSEdition -eq 'Core') {
            [System.IO.FileSystemAclExtensions]::SetAccessControl([IO.FileInfo]::new($Path), $acl)
        } else {
            [IO.File]::SetAccessControl($Path, $acl)
        }
        $aclOutput = & icacls.exe $Path '/verify' 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw ($aclOutput -join [Environment]::NewLine)
        }
    } catch {
        Write-Warning "Die Secret-ACL konnte für '$Path' nicht eingeschränkt werden: $($_.Exception.Message)"
    }
}

function Write-SecretFile([string]$Path, [string]$Value) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Value, $encoding)
    Protect-SecretFile $Path
}

function Initialize-Secret([string]$Name, [string]$Filename) {
    $path = Join-Path $env:PAPAGUI_SERVER_CONFIG_PATH $Filename
    $current = [Environment]::GetEnvironmentVariable($Name)
    Protect-SecretFile $path
    if (-not $current) {
        if (-not (Test-Path $path) -or -not (Get-Item $path).Length) {
            $current = & $python -c 'import secrets; print(secrets.token_urlsafe(32))'
            if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($current)) {
                throw 'Der API-Token konnte nicht erzeugt werden.'
            }
            Write-SecretFile $path $current
        } else {
            $current = Get-Content -Raw $path
        }
        [Environment]::SetEnvironmentVariable($Name, $current, 'Process')
    } else {
        Write-SecretFile $path $current
    }
}

if (-not $env:PAPAGUI_SOURCE_PATH) {
    $env:PAPAGUI_SOURCE_PATH = Join-Path $PSScriptRoot 'Bauvorhaben'
}
if (-not $env:PAPAGUI_SOURCE_MAPPINGS) {
    $mapping = @{}
    $mapping[$env:PAPAGUI_SOURCE_ID] = @{ windows = $env:PAPAGUI_SOURCE_PATH }
    $env:PAPAGUI_SOURCE_MAPPINGS = $mapping | ConvertTo-Json -Compress
}

$startLocalServer = & $python tools/prepare_client_start.py --local-server @prepareArguments
if ($LASTEXITCODE -ne 0) { throw 'Die gespeicherte Clientverbindung konnte nicht gelesen werden.' }
if ($startLocalServer -eq 'true') {
    Initialize-Secret 'PAPAGUI_API_TOKEN' 'api-token'
}
& $python tools/prepare_client_start.py --seed @prepareArguments
if ($LASTEXITCODE -ne 0) { throw 'Die Clientkonfiguration konnte nicht vorbereitet werden.' }

function Start-LocalServer([int]$HealthCheckAttempts = 600) {
    if ($env:PAPAGUI_SKIP_DOCKER -eq '1') { return }
    if (-not (Test-Path -LiteralPath $env:PAPAGUI_SOURCE_PATH -PathType Container)) {
        Write-Warning "Der Quellordner '$env:PAPAGUI_SOURCE_PATH' fehlt; Client startet offline."
        return
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Warning 'Docker wurde nicht gefunden. Bitte Docker Desktop installieren; Client startet offline.'
        return
    }

    try {
        # Windows PowerShell must also inspect native exit codes. Docker writes
        # normal build progress to stderr, which must not abort the launcher.
        $ErrorActionPreference = 'Continue'
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Docker ist nicht erreichbar. Bitte Docker Desktop starten; Client startet offline.'
            return
        }
        & docker compose version *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Docker Compose ist nicht verfuegbar. Bitte Docker Desktop aktualisieren; Client startet offline.'
            return
        }

        Write-Host 'PapaGUI-Server wird gebaut und gestartet. Beim ersten Start kann dies einige Minuten dauern.'
        # Compose builds a missing image and creates a missing container. Existing
        # builds use the cache; stopped containers are started again.
        & docker compose -f deploy/server/compose.yaml up -d --build papagui-server 2>&1 |
            ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Docker konnte den Server nicht bauen oder starten (Exitcode $LASTEXITCODE). Siehe Ausgabe oben; Client startet offline."
            return
        }
    } catch {
        Write-Warning "Der lokale Serverstart ist fehlgeschlagen: $($_.Exception.Message). Client startet offline."
        return
    }

    Write-Host 'Warte auf den Server-Healthcheck ...'
    foreach ($attempt in 1..$HealthCheckAttempts) {
        try {
            Invoke-RestMethod -Uri ($env:PAPAGUI_INDEX_SERVER_URL.TrimEnd('/') + '/health') -TimeoutSec 1 -ErrorAction Stop | Out-Null
            Write-Host 'PapaGUI-Server ist erreichbar.'
            return
        } catch {
            $runningContainer = & docker compose -f deploy/server/compose.yaml ps --status running --quiet papagui-server 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $runningContainer) {
                Write-Warning 'Der Servercontainer ist nach dem Compose-Start nicht mehr aktiv; Client startet offline.'
                return
            }
            Start-Sleep -Seconds 1
        }
    }
    Write-Warning 'Server-Healthcheck hat das Zeitlimit erreicht; Client startet offline.'
}

if ($startLocalServer -eq 'true') { Start-LocalServer }
# Children load the saved, editable values. Only explicit overrides are inherited.
foreach ($variable in $clientDefaultVariables) {
    [Environment]::SetEnvironmentVariable($variable, $null, 'Process')
}

function Start-DesktopClient {
    # The client owns tray startup. pythonw keeps both GUI processes console-free
    # and lets the batch/PowerShell bootstrap exit as soon as the GUI is launched.
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        throw 'pythonw.exe fehlt in der virtuellen Umgebung. Bitte die Python-Installation reparieren.'
    }
    Start-Process -FilePath $pythonw -ArgumentList (@('-m', 'papagui_client') + @($args)) -WindowStyle Hidden
}

if ('--sync-only' -in $args -or '--help' -in $args -or '-h' -in $args) {
    & $python -m papagui_client @args
    exit $LASTEXITCODE
}
Start-DesktopClient @args
exit 0
