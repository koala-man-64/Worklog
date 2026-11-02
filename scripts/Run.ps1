[CmdletBinding()]
param(
    [string]$PythonCommand = "python",
    [string]$DataDir,
    [ValidateRange(1, 65535)][int]$Port,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$setupScript = Join-Path $PSScriptRoot "Setup.ps1"
$worklogScript = Join-Path $PSScriptRoot "Worklog.ps1"
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
$worklogExe = Join-Path $projectRoot ".venv/Scripts/worklog.exe"

if (-not $DataDir -and -not $env:WORKLOG_DATA_DIR) {
    $userDataDir = [Environment]::GetEnvironmentVariable("WORKLOG_DATA_DIR", "User")
    if ($userDataDir) { $env:WORKLOG_DATA_DIR = $userDataDir }
}

$commonArgs = @{}
if ($DataDir) { $commonArgs["DataDir"] = $DataDir }
if ($Port) { $commonArgs["Port"] = $Port }

# Run Setup's shared-dependency check before stopping anything, so a refusal leaves Worklog running.
& $setupScript -CheckOnly
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$stoppedBeforeSetup = $false
if ((Test-Path -LiteralPath $pythonPath) -and (Test-Path -LiteralPath $worklogExe)) {
    & $worklogScript -Command stop @commonArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $stoppedBeforeSetup = $true

    # Snapshot the stopped database before Setup and the new version's first start, which may upgrade
    # its schema. A data directory with no database yet has nothing to keep.
    $state = & $worklogScript -Command check @commonArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $database = Join-Path ($state | Out-String | ConvertFrom-Json).dataDir "worklog.sqlite3"
    if (Test-Path -LiteralPath $database) {
        & $worklogScript -Command backup @commonArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

& $setupScript -PythonCommand $PythonCommand
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $stoppedBeforeSetup) {
    & $worklogScript -Command stop @commonArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $worklogScript -Command start @commonArgs -NoBrowser:$NoBrowser
exit $LASTEXITCODE
