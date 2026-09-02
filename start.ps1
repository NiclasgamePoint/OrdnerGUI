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
$env:PAPAGUI_INDEX_SERVER_URL = if ($env:PAPAGUI_INDEX_SERVER_URL) { $env:PAPAGUI_INDEX_SERVER_URL } else { 'http://127.0.0.1:8765' }
$env:PAPAGUI_CLIENT_DATA_ROOT = if ($env:PAPAGUI_CLIENT_DATA_ROOT) { $env:PAPAGUI_CLIENT_DATA_ROOT } else { Join-Path $PSScriptRoot 'client-data' }
$env:PAPAGUI_SERVER_DATA_PATH = if ($env:PAPAGUI_SERVER_DATA_PATH) { $env:PAPAGUI_SERVER_DATA_PATH } else { Join-Path $PSScriptRoot 'docker-server-data' }
$env:PAPAGUI_SERVER_CONFIG_PATH = if ($env:PAPAGUI_SERVER_CONFIG_PATH) { $env:PAPAGUI_SERVER_CONFIG_PATH } else { Join-Path $PSScriptRoot 'docker-config' }
$env:PAPAGUI_SOURCE_ID = if ($env:PAPAGUI_SOURCE_ID) { $env:PAPAGUI_SOURCE_ID } else { 'primary' }
New-Item -ItemType Directory -Force $env:PAPAGUI_CLIENT_DATA_ROOT, $env:PAPAGUI_SERVER_DATA_PATH, $env:PAPAGUI_SERVER_CONFIG_PATH | Out-Null

function Protect-SecretFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $aclOutput = & icacls.exe $Path '/inheritance:r' '/grant:r' "${identity}:(R,W)" 2>&1
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
            $current = & $python -c 'import secrets; print(secrets.token_urlsafe(32), end="")'
            Write-SecretFile $path $current
        } else {
            $current = Get-Content -Raw $path
        }
        [Environment]::SetEnvironmentVariable($Name, $current, 'Process')
    } else {
        Write-SecretFile $path $current
    }
}

Initialize-Secret 'PAPAGUI_API_TOKEN' 'api-token'

function Get-AdminPasswordHash([string]$Password) {
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $python
    $startInfo.Arguments = '-m papagui_server hash-password --stdin'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw 'Der Passwort-Hashprozess konnte nicht gestartet werden.' }
    $process.StandardInput.Write($Password)
    $process.StandardInput.Close()
    $output = $process.StandardOutput.ReadToEnd()
    $errorOutput = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "Der Passwort-Hashprozess ist fehlgeschlagen: $errorOutput"
    }
    return $output.Trim()
}

# Keep the bootstrap password out of child-process argv and the container
# environment. The readable development secret remains host-local for tray login.
Protect-SecretFile (Join-Path $env:PAPAGUI_SERVER_CONFIG_PATH 'admin-password')
if (-not $env:PAPAGUI_ADMIN_PASSWORD_HASH) {
    Initialize-Secret 'PAPAGUI_ADMIN_PASSWORD' 'admin-password'
    $password = $env:PAPAGUI_ADMIN_PASSWORD
    Remove-Item Env:PAPAGUI_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    $env:PAPAGUI_ADMIN_PASSWORD_HASH = Get-AdminPasswordHash $password
    $password = $null
}
$adminHashFile = Join-Path $env:PAPAGUI_SERVER_CONFIG_PATH 'admin-password-hash'
Write-SecretFile $adminHashFile $env:PAPAGUI_ADMIN_PASSWORD_HASH
Remove-Item Env:PAPAGUI_ADMIN_PASSWORD -ErrorAction SilentlyContinue
Remove-Item Env:PAPAGUI_ADMIN_PASSWORD_HASH -ErrorAction SilentlyContinue

if (-not $env:PAPAGUI_SOURCE_PATH) {
    $env:PAPAGUI_SOURCE_PATH = Join-Path $PSScriptRoot 'Bauvorhaben'
}
if (-not $env:PAPAGUI_SOURCE_MAPPINGS) {
    $mapping = @{}
    $mapping[$env:PAPAGUI_SOURCE_ID] = @{ windows = $env:PAPAGUI_SOURCE_PATH }
    $env:PAPAGUI_SOURCE_MAPPINGS = $mapping | ConvertTo-Json -Compress
}

$dockerStart = $null
if ($env:PAPAGUI_SKIP_DOCKER -ne '1' -and (Test-Path $env:PAPAGUI_SOURCE_PATH)) {
    try {
        docker info *> $null
        $dockerStart = Start-Process -FilePath 'docker' -ArgumentList @('compose', '-f', 'deploy/server/compose.yaml', 'up', '-d', '--build', 'papagui-server') -WindowStyle Hidden -PassThru
        Write-Host 'PapaGUI-Server wird im Hintergrund gebaut und gestartet.'
    } catch {
        Write-Warning 'Docker ist nicht erreichbar; der Client verwendet seinen letzten lokalen Stand.'
    }
}

if ($dockerStart) {
    Write-Host 'Warte auf den Server-Healthcheck ...'
    $healthy = $false
    foreach ($attempt in 1..600) {
        try {
            Invoke-RestMethod -Uri ($env:PAPAGUI_INDEX_SERVER_URL.TrimEnd('/') + '/health') -TimeoutSec 1 | Out-Null
            $healthy = $true
            Write-Host 'PapaGUI-Server ist erreichbar.'
            break
        } catch {
            $dockerStart.Refresh()
            if ($dockerStart.HasExited -and $dockerStart.ExitCode -ne 0) {
                Write-Warning 'Docker konnte den Servercontainer nicht starten.'
                break
            }
            if ($dockerStart.HasExited) {
                $runningContainer = & docker compose -f deploy/server/compose.yaml ps --status running --quiet papagui-server 2>$null
                if ($LASTEXITCODE -ne 0 -or -not $runningContainer) {
                    Write-Warning 'Der Servercontainer ist nach dem Compose-Start nicht mehr aktiv.'
                    break
                }
            }
            Start-Sleep -Seconds 1
        }
    }
    if (-not $healthy) {
        Write-Warning 'Server-Healthcheck nach 10 Minuten noch nicht erfolgreich; Client startet offline.'
    }
}

Start-Process -FilePath $python -ArgumentList @('-m', 'papagui_client.entrypoints.tray', '--background') -WindowStyle Hidden
& $python -m papagui_client @args
exit $LASTEXITCODE
