param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Relay\bin",
    [switch]$AddToPath = $true,
    [string]$RelayHome = "$env:LOCALAPPDATA\Relay"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Pyz = Join-Path $Root "relay.pyz"

$Python = Get-Command py -ErrorAction SilentlyContinue
if (-not $Python) {
    $Python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $Python) {
    throw "Python 3.11+ is required. Install Python and ensure py.exe or python.exe is on PATH."
}

# relay.pyz is a build artifact and is not checked into the repository, so a
# fresh clone has to build it first. Release assets ship it already built.
$Builder = Join-Path $Root "build_release.py"
if ((-not (Test-Path $Pyz)) -and (Test-Path $Builder)) {
    Write-Host "relay.pyz not found; building it from source..."
    & $Python.Source $Builder | Out-Null
}

if (-not (Test-Path $Pyz)) {
    throw "relay.pyz not found. Build it with: $($Python.Name) build_release.py"
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item $Pyz (Join-Path $InstallDir "relay.pyz") -Force

$Cmd = @"
@echo off
set "RELAY_HOME=$RelayHome"
py -3 "%~dp0relay.pyz" %*
"@
Set-Content -Path (Join-Path $InstallDir "relay.cmd") -Value $Cmd -Encoding ASCII

$Ps = @"
`$env:RELAY_HOME = "$RelayHome"
& py -3 "`$PSScriptRoot\relay.pyz" @args
exit `$LASTEXITCODE
"@
Set-Content -Path (Join-Path $InstallDir "relay.ps1") -Value $Ps -Encoding UTF8

if ($AddToPath) {
    $Current = [Environment]::GetEnvironmentVariable("Path", "User")
    $Parts = @($Current -split ';' | Where-Object { $_ })
    if ($Parts -notcontains $InstallDir) {
        [Environment]::SetEnvironmentVariable("Path", (($Parts + $InstallDir) -join ';'), "User")
        Write-Host "Added to user PATH: $InstallDir"
    }
}

$env:RELAY_HOME = $RelayHome
& py -3 (Join-Path $InstallDir "relay.pyz") init

Write-Host ""
Write-Host "Relay installed: $InstallDir"
Write-Host "Open a new terminal and run:"
Write-Host "  relay doctor --worker claude --deep"
Write-Host "  relay doctor --worker codex --deep"
Write-Host "Antigravity remains disabled until its deep doctor passes."
