# Graphify launcher for the isolated uv tool and the user-bound Gemini credential.
# Keep native CLI arguments verbatim: PowerShell parameter abbreviation would
# otherwise confuse Graphify's --graph with a GraphifyArgs parameter.
$GraphifyArgs = @($args)
$Build = $GraphifyArgs.Count -eq 1 -and $GraphifyArgs[0] -eq '-Build'
$CheckGemini = $GraphifyArgs.Count -eq 1 -and $GraphifyArgs[0] -eq '-CheckGemini'
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$uv = Get-Command uv -ErrorAction Stop
$toolDirectory = & $uv.Source --no-cache tool dir
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the uv tool directory.' }
$graphifyPython = Join-Path $toolDirectory 'graphifyy/Scripts/python.exe'
$graphifyExe = Join-Path $toolDirectory 'graphifyy/Scripts/graphify.exe'
if (-not (Test-Path -LiteralPath $graphifyPython)) {
    throw 'Install Graphify with: uv tool install "graphifyy[gemini]==0.9.53"'
}

$previousKey = $env:GEMINI_API_KEY
$previousEncoding = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = '1'
    if ($Build) {
        & $graphifyPython (Join-Path $PSScriptRoot 'build_source_graph.py')
    } else {
        $secretPath = Join-Path $env:LOCALAPPDATA 'PapaGUI/dev-secrets/gemini-key.dpapi'
        $needsGemini = $CheckGemini -or ($GraphifyArgs.Count -gt 0 -and $GraphifyArgs[0] -eq 'extract')
        if ($needsGemini -and -not $env:GEMINI_API_KEY -and (Test-Path -LiteralPath $secretPath)) {
            $secureKey = (Get-Content -LiteralPath $secretPath -Raw).Trim() | ConvertTo-SecureString
            $env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
        }
        if ($CheckGemini) {
            & $graphifyPython (Join-Path $PSScriptRoot 'check_graphify_gemini.py')
        } else {
            if (-not $GraphifyArgs) { $GraphifyArgs = @('--help') }
            Push-Location $projectRoot
            try { & $graphifyExe @GraphifyArgs } finally { Pop-Location }
        }
    }
    $result = $LASTEXITCODE
} finally {
    $env:GEMINI_API_KEY = $previousKey
    $env:PYTHONUTF8 = $previousEncoding
}
exit $result
