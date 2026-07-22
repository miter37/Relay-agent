param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Relay\bin",
    [switch]$KeepData = $true,
    [string]$RelayHome = "$env:LOCALAPPDATA\Relay"
)

$ErrorActionPreference = "Stop"
try { relay daemon stop --machine | Out-Null } catch {}

$Current = [Environment]::GetEnvironmentVariable("Path", "User")
$Parts = @($Current -split ';' | Where-Object { $_ -and $_ -ne $InstallDir })
[Environment]::SetEnvironmentVariable("Path", ($Parts -join ';'), "User")

if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
if (-not $KeepData -and (Test-Path $RelayHome)) { Remove-Item $RelayHome -Recurse -Force }
Write-Host "Relay uninstalled."
