$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvActivate = Join-Path $PSScriptRoot '.venv/Scripts/Activate.ps1'
$legacyActivate = Join-Path $PSScriptRoot 'venv/Scripts/Activate.ps1'

if (Test-Path $venvActivate) {
    . $venvActivate
} elseif (Test-Path $legacyActivate) {
    . $legacyActivate
} else {
    Write-Error 'Keine virtuelle Umgebung gefunden (.venv oder venv).'
    exit 1
}

python main.py
exit $LASTEXITCODE
