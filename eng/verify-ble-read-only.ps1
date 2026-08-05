[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$infrastructure = Join-Path $projectRoot 'src\TreadmillRunner.Infrastructure'
$sourceRoot = Join-Path $projectRoot 'src'

function Get-CSharpFiles {
    param(
        [Parameter(Mandatory)]
        [string] $Root,

        [string] $ExcludedRoot
    )

    $excludedPrefix = if ($ExcludedRoot) {
        [System.IO.Path]::GetFullPath($ExcludedRoot).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    }
    else {
        $null
    }

    @(Get-ChildItem -LiteralPath $Root -Recurse -File -Filter '*.cs' | Where-Object {
        -not $excludedPrefix -or
        -not [System.IO.Path]::GetFullPath($_.FullName).StartsWith(
            $excludedPrefix,
            [System.StringComparison]::OrdinalIgnoreCase)
    })
}

function Find-CSharpMatches {
    param(
        [Parameter(Mandatory)]
        [System.IO.FileInfo[]] $Files,

        [Parameter(Mandatory)]
        [string] $Pattern,

        [switch] $SimpleMatch
    )

    if ($Files.Count -eq 0) {
        return @()
    }

    @(Select-String -LiteralPath $Files.FullName -Pattern $Pattern -SimpleMatch:$SimpleMatch)
}

$infrastructureFiles = @(Get-CSharpFiles -Root $infrastructure)

$prohibitedPlatformCalls = @(
    'PairAsync',
    'UnpairAsync',
    'RequestAccessAsync'
)

foreach ($call in $prohibitedPlatformCalls) {
    $matches = @(Find-CSharpMatches -Files $infrastructureFiles -Pattern $call -SimpleMatch)
    if ($matches.Count -gt 0) {
        throw "TR-002 read-only boundary violation: found platform call '$call'."
    }
}

$commandOwner = [System.IO.Path]::GetFullPath(
    (Join-Path $infrastructure 'Bluetooth\WindowsBleCommandConnection.cs'))
foreach ($writeCall in @('WriteValueAsync', 'WriteValueWithResultAsync')) {
    $writeMatches = @(Find-CSharpMatches -Files $infrastructureFiles -Pattern $writeCall -SimpleMatch)
    foreach ($match in @($writeMatches | ForEach-Object { $_.Path } | Sort-Object -Unique)) {
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
$descriptorMatches = @(Find-CSharpMatches `
    -Files $infrastructureFiles `
    -Pattern 'WriteClientCharacteristicConfigurationDescriptorAsync' `
    -SimpleMatch)
foreach ($match in @($descriptorMatches | ForEach-Object { $_.Path } | Sort-Object -Unique)) {
    if ([System.IO.Path]::GetFullPath($match) -notin $descriptorOwners) {
        throw "BLE boundary violation: notification descriptor writes must remain in the read-only or command connection owners."
    }
}

$nonInfrastructureFiles = @(Get-CSharpFiles -Root $sourceRoot -ExcludedRoot $infrastructure)
$winRtMatches = @(Find-CSharpMatches `
    -Files $nonInfrastructureFiles `
    -Pattern 'Windows\.Devices\.Bluetooth')
if ($winRtMatches.Count -gt 0) {
    throw 'WinRT Bluetooth types must remain inside TreadmillRunner.Infrastructure.'
}

Write-Host 'BLE read-only, serialized command-write, and WinRT ownership boundaries passed.'
