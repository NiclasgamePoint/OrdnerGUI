param(
    [string]$Python = '.\build\release-venv\Scripts\python.exe',
    [switch]$BuildClient,
    [string]$Iscc = '.\build\inno-setup\ISCC.exe'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$Python = (Resolve-Path -LiteralPath $Python).Path
$env:QT_QPA_PLATFORM = 'offscreen'
function Invoke-Python {
    & $Python @args
    if ($LASTEXITCODE -ne 0) { throw "Check failed: python $args (exit $LASTEXITCODE)" }
}
Invoke-Python -c 'import sys; assert sys.version_info[:2] == (3, 11), "Release checks require Python 3.11"'
Invoke-Python -m pip check
Invoke-Python tools/release_metadata.py
Invoke-Python -m ruff check packages tests tools main.py
Invoke-Python tests/run_ci.py --coverage
Invoke-Python tools/export_openapi.py --check
Invoke-Python tools/graphify_refresh.py --check
$distributionOutput = Join-Path $projectRoot ('build/release-distributions/' + [guid]::NewGuid().ToString('N'))
foreach ($component in @('contracts', 'server', 'client')) {
    Invoke-Python -m build --no-isolation --outdir $distributionOutput "packages/$component"
}
Invoke-Python tools/check_artifacts.py $distributionOutput
if ($BuildClient) {
    Invoke-Python -m PyInstaller --clean --noconfirm packaging/client/pyinstaller/papagui-client.spec
    Invoke-Python -m PyInstaller --clean --noconfirm packaging/client/pyinstaller/papagui-tray.spec
    Invoke-Python tools/check_frozen_client.py dist
    $installerOutput = Join-Path $projectRoot ('dist/installers-' + [guid]::NewGuid().ToString('N'))
    Invoke-Python tools/build_installers.py --iscc $Iscc --output $installerOutput
    Write-Host "Installer: $installerOutput"
}
Write-Host 'Local Windows checks passed. Linux/macOS and Docker require separate native checks.'
