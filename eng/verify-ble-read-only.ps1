[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$infrastructure = Join-Path $projectRoot 'src\TreadmillRunner.Infrastructure'
$sourceRoot = Join-Path $projectRoot 'src'

$prohibitedPlatformCalls = @(
    'PairAsync',
    'UnpairAsync',
    'RequestAccessAsync'
)

foreach ($call in $prohibitedPlatformCalls) {
    & rg --line-number --glob '*.cs' --fixed-strings $call $infrastructure
    if ($LASTEXITCODE -eq 0) {
        throw "TR-002 read-only boundary violation: found platform call '$call'."
    }
    if ($LASTEXITCODE -gt 1) {
        throw "Unable to inspect Infrastructure for '$call'."
    }
}

$commandOwner = [System.IO.Path]::GetFullPath(
    (Join-Path $infrastructure 'Bluetooth\WindowsBleCommandConnection.cs'))
foreach ($writeCall in @('WriteValueAsync', 'WriteValueWithResultAsync')) {
    $writeMatches = & rg --files-with-matches --glob '*.cs' --fixed-strings $writeCall $infrastructure
    if ($LASTEXITCODE -gt 1) {
        throw "Unable to inspect Infrastructure for '$writeCall'."
    }
    foreach ($match in $writeMatches) {
        if ([System.IO.Path]::GetFullPath($match) -ne $commandOwner) {
            throw "BLE command boundary violation: characteristic writes must remain in '$commandOwner'."
        }
    }
}

# Enabling notifications necessarily writes the standard Client Characteristic
# Configuration Descriptor. Keep that narrowly owned by the read-only connection;
# characteristic-value writes remain prohibited everywhere above.
$subscriptionOwner = [System.IO.Path]::GetFullPath(
    (Join-Path $infrastructure 'Bluetooth\WindowsBleReadOnlyConnection.cs'))
$descriptorOwners = @($subscriptionOwner, $commandOwner)
$descriptorMatches = & rg --files-with-matches --glob '*.cs' --fixed-strings `
    'WriteClientCharacteristicConfigurationDescriptorAsync' $infrastructure
if ($LASTEXITCODE -gt 1) {
    throw 'Unable to inspect Infrastructure for notification descriptor writes.'
}
foreach ($match in $descriptorMatches) {
    if ([System.IO.Path]::GetFullPath($match) -notin $descriptorOwners) {
        throw "BLE boundary violation: notification descriptor writes must remain in the read-only or command connection owners."
    }
}

& rg --line-number --glob '*.cs' --glob '!**/TreadmillRunner.Infrastructure/**' 'Windows\.Devices\.Bluetooth' $sourceRoot
if ($LASTEXITCODE -eq 0) {
    throw 'WinRT Bluetooth types must remain inside TreadmillRunner.Infrastructure.'
}
if ($LASTEXITCODE -gt 1) {
    throw 'Unable to inspect the source boundary for WinRT Bluetooth types.'
}

Write-Host 'BLE read-only, serialized command-write, and WinRT ownership boundaries passed.'
