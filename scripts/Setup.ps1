[CmdletBinding()]
param([string]$PythonCommand = "python", [switch]$CheckOnly)
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 turns native stderr into a terminating error under "Stop" when output is
# redirected (npm config warnings, error messages the tools print). Keep "Stop" for cmdlets and
# judge native commands by $LASTEXITCODE instead.
function Invoke-Native([scriptblock]$Run) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Run } finally { $ErrorActionPreference = $previous }
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv/Scripts/python.exe"

# Every checkout and worktree installs its own .venv and frontend/node_modules. Setup replaces both,
# so it stops while they link into another checkout or another worktree links into them.
$dependencies = @(".venv", "frontend/node_modules")
function Get-LinkTarget([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not ($item -and $item.LinkType)) { return }
    $target = [string]@($item.Target)[0]
    if (-not [IO.Path]::IsPathRooted($target)) { $target = Join-Path (Split-Path -Parent $Path) $target }
    [IO.Path]::GetFullPath($target)
}
$shared = @(foreach ($name in $dependencies) {
    $target = Get-LinkTarget (Join-Path $projectRoot $name)
    if ($target) { "$name in this checkout links to $target" }
})
if (Get-Command git -ErrorAction SilentlyContinue) {
    $own = @(foreach ($name in $dependencies) { [IO.Path]::GetFullPath((Join-Path $projectRoot $name)) })
    $worktrees = Invoke-Native { & git -C $projectRoot worktree list --porcelain 2>$null }
    if ($LASTEXITCODE -eq 0) {
        foreach ($line in $worktrees | Where-Object { $_ -like "worktree *" }) {
            foreach ($name in $dependencies) {
                $link = Join-Path $line.Substring(9) $name
                if ($own -contains (Get-LinkTarget $link)) { $shared += "$link links into this checkout" }
            }
        }
    }
}
if ($shared.Count) {
    throw ("Setup replaces .venv and frontend/node_modules, and another checkout shares them:`n  " + ($shared -join "`n  ") +
        "`nRun Setup.ps1 in each worktree to give it its own. Remove a link with rmdir, which leaves its target alone.")
}
if ($CheckOnly) { exit 0 }

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Invoke-Native { & $PythonCommand -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12 or newer is required'" }
    if ($LASTEXITCODE -ne 0) { throw "Python prerequisite check failed" }
    Invoke-Native { & $PythonCommand -m venv (Join-Path $projectRoot ".venv") }
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
}
Invoke-Native { & $pythonPath -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12 or newer is required'" }
if ($LASTEXITCODE -ne 0) { throw "Existing virtual environment has an unsupported Python version" }
Invoke-Native { & $pythonPath -m pip install --no-input --disable-pip-version-check -r (Join-Path $projectRoot "requirements.lock") }
if ($LASTEXITCODE -ne 0) { throw "Locked Python dependency installation failed" }
Invoke-Native { & $pythonPath -m pip install --no-input --disable-pip-version-check --no-deps --no-build-isolation -e $projectRoot }
if ($LASTEXITCODE -ne 0) { throw "Worklog installation failed" }
Push-Location (Join-Path $projectRoot "frontend")
try {
    Invoke-Native { & npm.cmd ci }
    if ($LASTEXITCODE -ne 0) { throw "Locked frontend dependency installation failed" }
    Invoke-Native { & npm.cmd run build }
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
} finally { Pop-Location }
Write-Host "Worklog setup complete. Run ./scripts/Worklog.ps1 start"
