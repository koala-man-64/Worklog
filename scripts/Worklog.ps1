[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [ValidateSet("start", "stop", "check", "backup", "backups", "restore", "export")]
    [string]$Command = "start",
    [string]$DataDir,
    [ValidateRange(1, 65535)][int]$Port,
    [Parameter(Position=1)][string]$File,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Run ./scripts/Setup.ps1 first" }
$worklogArgs = @("-m", "worklog.cli")
if ($DataDir) { $worklogArgs += @("--data-dir", $DataDir) }
if ($Port) { $worklogArgs += @("--port", "$Port") }
$worklogArgs += $Command
if ($File) { $worklogArgs += $File }
& $pythonPath @worklogArgs
$worklogExitCode = $LASTEXITCODE
if ($Command -eq "start" -and $worklogExitCode -eq 0 -and -not $NoBrowser) {
    # Same precedence as the CLI: -Port, then WORKLOG_PORT, then 8877.
    $browserPort = if ($Port) { $Port } elseif ($env:WORKLOG_PORT) { $env:WORKLOG_PORT } else { 8877 }
    Start-Process "http://127.0.0.1:$browserPort"
}
exit $worklogExitCode
