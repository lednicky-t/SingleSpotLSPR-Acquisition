$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$localPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$suiteRoot = (Resolve-Path (Join-Path $projectRoot "..\..\..")).Path
$sharedPython = Join-Path $suiteRoot ".venv\Scripts\python.exe"
$entryPoint = Join-Path $projectRoot "src\main.py"

if (Test-Path $localPython) {
    & $localPython $entryPoint
    exit $LASTEXITCODE
}

if (Test-Path $sharedPython) {
    Write-Host "Local .venv not ready, using shared LSPR Acquisition environment." -ForegroundColor Yellow
    & $sharedPython $entryPoint
    exit $LASTEXITCODE
}

Write-Error "No usable Python environment found. Create .venv or install dependencies first."
exit 1
