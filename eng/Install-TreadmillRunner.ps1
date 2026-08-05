#Requires -RunAsAdministrator
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
trap {
    Write-Host ''
    Write-Host 'TreadmillRunner was not installed.' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Press Enter to close this window'
    exit 1
}

Write-Host 'TreadmillRunner setup' -ForegroundColor Cyan
Write-Host '1/3 Checking this Windows computer...'
$bundleRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$metadataPath = Join-Path $bundleRoot 'release.json'
if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
    throw 'release.json is missing. Extract the complete installer ZIP before running this script.'
}
$metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
if ([string]$metadata.version -notmatch '^\d+\.\d+\.\d+$') { throw 'The installer version is invalid.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'TreadmillRunner requires 64-bit Windows 11.' }
$runtimeList = @(& dotnet --list-runtimes 2>$null)
if ($LASTEXITCODE -ne 0 -or -not ($runtimeList -match '^Microsoft\.AspNetCore\.App 10\.')) {
    throw 'Install the Microsoft ASP.NET Core Runtime 10 (Windows x64) from https://dotnet.microsoft.com/download/dotnet/10.0 and run this installer again.'
}
$privateProfiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object NetworkCategory -eq 'Private')
if ($privateProfiles.Count -eq 0) {
    throw 'Set the NUC network profile to Private before installation. TreadmillRunner must not be exposed to a public network.'
}

$installer = Join-Path $bundleRoot 'install-gateway-service.ps1'
$application = Join-Path $bundleRoot 'app'
$certificate = Join-Path $bundleRoot 'treadmillrunner-release-signing.cer'
foreach ($required in @($installer, $application, $certificate)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "The installer bundle is incomplete: $required" }
}

Write-Host '2/3 Installing the Windows service and private-network access...'
& $installer `
    -Version ([string]$metadata.version) `
    -ReleasePath $application `
    -PublicCertificatePath $certificate

Write-Host ''
Write-Host '3/3 Verifying and opening TreadmillRunner...'
Write-Host "TreadmillRunner $($metadata.version) is ready."
Write-Host 'This NUC: http://localhost:5180'
Write-Host "Household devices: http://$($env:COMPUTERNAME):5180"
Start-Process 'http://localhost:5180'
