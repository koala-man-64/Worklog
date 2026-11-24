[CmdletBinding()]
param(
    [string]$PythonCommand = "python",
    [string]$DataDir,
    [ValidateRange(1, 65535)][int]$Port,
    [switch]$NoBrowser,
    # Serve a data folder another checkout last served, and record this one (as after moving the checkout).
    [switch]$Force
)
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$setupScript = Join-Path $PSScriptRoot "Setup.ps1"
$worklogScript = Join-Path $PSScriptRoot "Worklog.ps1"
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
$worklogExe = Join-Path $projectRoot ".venv/Scripts/worklog.exe"

# Without -DataDir or WORKLOG_DATA_DIR, the CLI reads the user-level WORKLOG_DATA_DIR itself, and
# refuses to start an empty folder nobody named beside one that holds a database: copying the
# variable in here would name it and skip that check.

$commonArgs = @{}
if ($DataDir) { $commonArgs["DataDir"] = $DataDir }
if ($Port) { $commonArgs["Port"] = $Port }

# Run Setup's shared-dependency check before stopping anything, so a refusal leaves Worklog running.
& $setupScript -CheckOnly
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Before anything is stopped: refuse a data folder another checkout last served, so running this
# script in an agent's worktree never stops the person's server to serve that worktree's code.
function Assert-ServedHere {
    $state = & $worklogScript -Command check @commonArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $report = $state | Out-String | ConvertFrom-Json
    if ($report.servedBy -and -not $report.servedByThisCheckout -and -not $Force) {
        Write-Error ("$($report.dataDir) was last served by the checkout $($report.servedBy), not this one " +
            "($projectRoot). Nothing was stopped. Run this from that checkout, or pass -Force if this " +
            "checkout should serve it from now on.") -ErrorAction Continue
        exit 1
    }
}

$stoppedBeforeSetup = $false
if ((Test-Path -LiteralPath $pythonPath) -and (Test-Path -LiteralPath $worklogExe)) {
    Assert-ServedHere
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
    Assert-ServedHere
    & $worklogScript -Command stop @commonArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $worklogScript -Command start @commonArgs -NoBrowser:$NoBrowser -Force:$Force
exit $LASTEXITCODE
