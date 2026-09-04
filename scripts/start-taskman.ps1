<#
.SYNOPSIS
    Start an installed Taskman, or the development environment in this checkout.
.DESCRIPTION
    Keeps failures visible. Install the downloaded source with install.ps1 first.
#>
param(
    [switch]$RuntimeCheck,
    [switch]$NoPause,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TaskmanArgs
)

$taskmanRoot = Split-Path -Parent $PSScriptRoot
$taskmanPython = Join-Path $taskmanRoot '.venv\Scripts\python.exe'
$taskmanBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) 'AppData\Local' }
$taskmanUserPython = Join-Path $taskmanBase 'Taskman\app\venv\Scripts\python.exe'

function Stop-TaskmanLaunch([int]$ExitCode) {
    if ($ExitCode -ne 0) {
        Write-Host ''
        Write-Host "Taskman exited with code $ExitCode. The error is shown above." -ForegroundColor Red
        Write-Host 'The error message includes the diagnostic report location when available.' -ForegroundColor DarkGray
        if (-not $NoPause -and [Environment]::UserInteractive) {
            try { Read-Host 'Press Enter to close this window' | Out-Null } catch { }
        }
    }
    exit $ExitCode
}

if (-not (Test-Path -LiteralPath $taskmanPython -PathType Leaf)) {
    if (-not (Test-Path -LiteralPath $taskmanUserPython -PathType Leaf)) {
        Write-Host 'Install Taskman first:' -ForegroundColor Yellow
        Write-Host "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\install.ps1`""
        Stop-TaskmanLaunch 1
    }
    $taskmanPython = $taskmanUserPython
}

try {
    & $taskmanPython -c 'import textual, rich; release = tuple(map(int, textual.__version__.split(chr(46))[:2])); assert (8, 2) <= release < (9, 0)'
    $taskmanExitCode = $LASTEXITCODE
    if ($taskmanExitCode -ne 0) {
        Write-Host "Taskman's dependencies need attention for: $taskmanPython" -ForegroundColor Yellow
        Write-Host "Repair with: powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\install.ps1`""
    } elseif ($RuntimeCheck) {
        Write-Host "Taskman runtime ready: $taskmanPython" -ForegroundColor Green
    } else {
        & $taskmanPython -m tui @TaskmanArgs
        $taskmanExitCode = $LASTEXITCODE
    }
} catch {
    Write-Host $_ -ForegroundColor Red
    $taskmanExitCode = 1
}
Stop-TaskmanLaunch $taskmanExitCode
