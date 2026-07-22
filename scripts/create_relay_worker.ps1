param(
    [string]$UserName = "RelayWorker",
    [string]$RelayHome = "D:\Relay",
    [SecureString]$Password
)

$ErrorActionPreference = "Stop"

if (-not $Password) {
    $Password = Read-Host "Password for local user $UserName" -AsSecureString
}

if (-not (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue)) {
    New-LocalUser -Name $UserName -Password $Password -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
    Write-Host "Created local user: $UserName"
}

New-Item -ItemType Directory -Force -Path $RelayHome | Out-Null

# Remove inherited broad write permissions only after reviewing this command in your environment.
icacls $RelayHome /inheritance:r | Out-Null
icacls $RelayHome /grant:r "$($env:USERNAME):(OI)(CI)F" | Out-Null
icacls $RelayHome /grant:r "$($UserName):(OI)(CI)M" | Out-Null
icacls $RelayHome /grant:r "SYSTEM:(OI)(CI)F" | Out-Null

$Folders = @("input", "requests", "workspace", "staging", "results", "artifacts", "logs", "runtime", "adapter-specs", "config")
foreach ($Folder in $Folders) {
    New-Item -ItemType Directory -Force -Path (Join-Path $RelayHome $Folder) | Out-Null
}

Write-Host "Relay root ACL configured: $RelayHome"
Write-Host "Next steps:"
Write-Host "1. Sign in as $UserName."
Write-Host "2. Install/login claude, codex, and optionally agy under that account."
Write-Host "3. Set RELAY_HOME=$RelayHome."
Write-Host "4. Review that this account cannot access personal/company sensitive directories."
Write-Host "5. Run relay config set service_isolation_acknowledged true."
Write-Host "6. Run relay doctor --deep."
Write-Host "7. For Antigravity, set workers.antigravity.security_verified true only after review."
