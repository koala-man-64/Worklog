[CmdletBinding()]
param([string]$PythonCommand = "python")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    & $PythonCommand -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12 or newer is required'"
    if ($LASTEXITCODE -ne 0) { throw "Python prerequisite check failed" }
    & $PythonCommand -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
}
& $pythonPath -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12 or newer is required'"
if ($LASTEXITCODE -ne 0) { throw "Existing virtual environment has an unsupported Python version" }
& $pythonPath -m pip install --no-input --disable-pip-version-check -r (Join-Path $projectRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Locked Python dependency installation failed" }
& $pythonPath -m pip install --no-input --disable-pip-version-check --no-deps --no-build-isolation -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw "Worklog installation failed" }
Push-Location (Join-Path $projectRoot "frontend")
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "Locked frontend dependency installation failed" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
} finally { Pop-Location }
Write-Host "Worklog setup complete. Run ./scripts/Worklog.ps1 start"
