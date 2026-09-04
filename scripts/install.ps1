<# Install this downloaded source package in a per-user environment. #>
param(
    [string]$Python = "",
    [string]$InstallDir = "",
    [switch]$NoLaunch,
    [string]$Vault = ""
)
$ErrorActionPreference = "Stop"
$taskmanSource = Split-Path -Parent $PSScriptRoot
if (-not $InstallDir) {
    $taskmanBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) 'AppData\Local' }
    $InstallDir = Join-Path $taskmanBase 'Taskman\app'
}
if (-not $Python) {
    $taskmanPy = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($taskmanPy) { $Python = $taskmanPy.Source } else {
        $taskmanPy = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($taskmanPy) { $Python = $taskmanPy.Source }
    }
}
if (-not $Python) { throw 'Python 3.10+ was not found. Use the standalone Windows download, or install Python and run this command again.' }
& $Python -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Taskman needs Python 3.10 or newer.' }
$taskmanEnv = Join-Path $InstallDir 'venv'
& $Python -m venv $taskmanEnv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Taskman environment.' }
$taskmanPython = Join-Path $taskmanEnv 'Scripts\python.exe'
& $taskmanPython -m pip install --upgrade $taskmanSource
if ($LASTEXITCODE -ne 0) { throw 'Installation failed. The existing vault files were not changed.' }
$taskmanCommand = Join-Path $InstallDir 'taskman.cmd'
$taskmanExe = Join-Path $taskmanEnv 'Scripts\taskman.exe'
@('@echo off', '"%~dp0venv\Scripts\taskman.exe" %*', 'exit /b %errorlevel%') | Set-Content -LiteralPath $taskmanCommand -Encoding ascii
Write-Host "Installed Taskman. Launch with: & `"$taskmanCommand`"" -ForegroundColor Green
Write-Host 'The environment is separate from your vault. Re-run this installer to upgrade.'
if (-not $NoLaunch) {
    if ($Vault) { & $taskmanExe --vault $Vault } else { & $taskmanExe }
    exit $LASTEXITCODE
}
