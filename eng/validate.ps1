[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [switch] $IncludeConnectIq
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$solution = Join-Path $projectRoot 'TreadmillRunner.slnx'

Push-Location $projectRoot
try {
    dotnet restore $solution --locked-mode
    if ($LASTEXITCODE -ne 0) { throw 'Locked restore failed. Run eng/bootstrap.ps1 after intentional package changes.' }

    dotnet format $solution --verify-no-changes --no-restore
    if ($LASTEXITCODE -ne 0) { throw 'Formatting or analyzer verification failed.' }

    python -B (Join-Path $projectRoot 'tools/garmin/test_adapter_contract.py')
    if ($LASTEXITCODE -ne 0) { throw 'Garmin adapter contract fixtures failed.' }
    & (Join-Path $PSScriptRoot 'validate-public-evidence.ps1')
    if ($IncludeConnectIq) {
        & (Join-Path $PSScriptRoot 'validate-connectiq.ps1')
    }
    else {
        Write-Host 'Connect IQ validation skipped; use -IncludeConnectIq only for companion-related changes.'
    }
    & (Join-Path $PSScriptRoot 'verify-ble-read-only.ps1')
    & (Join-Path $PSScriptRoot 'build.ps1') -Configuration $Configuration
    & (Join-Path $PSScriptRoot 'test.ps1') -Configuration $Configuration

    Write-Host 'TreadmillRunner deterministic validation passed.'
}
finally {
    Pop-Location
}
