#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][string] $SourceFeed,
    [Parameter(Mandatory)][string] $DestinationFeed
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath($SourceFeed)
$destinationRoot = [System.IO.Path]::GetFullPath($DestinationFeed)
if ($destinationRoot.StartsWith(
    [System.IO.Path]::GetFullPath("$env:ProgramData\TreadmillRunner\updates\feed"),
    [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Acceptance fixtures cannot be selected into the daily ProgramData stable feed. Use an isolated acceptance DataRoot.'
}
$manifestSource = Join-Path $sourceRoot "stable-$Version.manifest.json"
if (-not (Test-Path -LiteralPath $manifestSource -PathType Leaf)) {
    throw "The acceptance manifest for $Version is missing."
}
$manifest = Get-Content -LiteralPath $manifestSource -Raw | ConvertFrom-Json
if ([string]$manifest.version -ne $Version -or [string]$manifest.channel -ne 'stable') {
    throw 'The acceptance manifest identity does not match the requested fixture.'
}
$packageName = [System.IO.Path]::GetFileName([string]$manifest.packageFileName)
if ($packageName -ne [string]$manifest.packageFileName -or -not $packageName.EndsWith('.zip', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The acceptance package name is invalid.'
}
$packageSource = Join-Path $sourceRoot $packageName
if (-not (Test-Path -LiteralPath $packageSource -PathType Leaf)) {
    throw 'The acceptance package is missing.'
}
$actualHash = (Get-FileHash -LiteralPath $packageSource -Algorithm SHA256).Hash
if ($actualHash -ne [string]$manifest.packageSha256) {
    throw 'The acceptance package hash does not match its signed manifest.'
}
New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
Copy-Item -LiteralPath $packageSource -Destination (Join-Path $destinationRoot $packageName) -Force
Copy-Item -LiteralPath $manifestSource -Destination (Join-Path $destinationRoot 'stable.manifest.json') -Force
Write-Host "Selected signed acceptance fixture $Version."
