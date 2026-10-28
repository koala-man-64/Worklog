[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet("start", "stop", "check", "backup", "backups", "restore", "export")]
    [string]$Command = "start",
    [string]$DataDir,
    [Parameter(Position=1)][string]$File,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Run ./scripts/Setup.ps1 first" }
$worklogArgs = @("-m", "worklog.cli")
if ($DataDir) { $worklogArgs += @("--data-dir", $DataDir) }
$worklogArgs += $Command
if ($File) { $worklogArgs += $File }
& $pythonPath @worklogArgs
$worklogExitCode = $LASTEXITCODE
if ($Command -eq "start" -and $worklogExitCode -eq 0 -and -not $NoBrowser) {
    Start-Process "http://127.0.0.1:8877"
}
exit $worklogExitCode
