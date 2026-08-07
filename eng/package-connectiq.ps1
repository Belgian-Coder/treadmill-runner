[CmdletBinding()]
param(
    [string] $DeveloperKey,
    [string] $SdkPath,
    [string] $OutputDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $projectRoot 'connectiq/TreadmillRunnerCompanion'
$validationScript = Join-Path $PSScriptRoot 'validate-connectiq.ps1'

function Resolve-ConnectIqSdk {
    param([string] $ExplicitPath)

    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) { $candidates.Add($ExplicitPath) }

    $currentSdk = Join-Path $env:APPDATA 'Garmin/ConnectIQ/current-sdk.cfg'
    if (Test-Path -LiteralPath $currentSdk -PathType Leaf) {
        $configured = (Get-Content -LiteralPath $currentSdk -Raw).Trim()
        if (-not [string]::IsNullOrWhiteSpace($configured)) { $candidates.Add($configured) }
    }

    foreach ($candidate in $candidates) {
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if (Test-Path -LiteralPath (Join-Path $resolved 'bin/monkeyc.bat') -PathType Leaf) {
            return $resolved
        }
    }

    throw 'Garmin Connect IQ SDK Manager does not have an active SDK.'
}

function Resolve-DeveloperKey {
    param([string] $ExplicitPath)

    $candidates = @(
        $ExplicitPath,
        $env:TREADMILLRUNNER_CONNECTIQ_DEVELOPER_KEY,
        (Join-Path $env:LOCALAPPDATA 'TreadmillRunner/secrets/connectiq/developer_key.der')
    )
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }

    throw 'A protected Connect IQ developer key is required.'
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectRoot 'artifacts/connectiq/release'
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$validationArguments = @{ RequireSdk = $true }
if (-not [string]::IsNullOrWhiteSpace($DeveloperKey)) { $validationArguments.DeveloperKey = $DeveloperKey }
if (-not [string]::IsNullOrWhiteSpace($SdkPath)) { $validationArguments.SdkPath = $SdkPath }
& $validationScript @validationArguments

$resolvedSdk = Resolve-ConnectIqSdk -ExplicitPath $SdkPath
$resolvedKey = Resolve-DeveloperKey -ExplicitPath $DeveloperKey
$compiler = Join-Path $resolvedSdk 'bin/monkeyc.bat'
$packagePath = Join-Path $resolvedOutput 'TreadmillRunnerCompanion.iq'

$compilerOutput = @(& $compiler @(
    '-e',
    '-f', (Join-Path $appRoot 'monkey.jungle'),
    '-o', $packagePath,
    '-y', $resolvedKey,
    '-r',
    '-w'
) 2>&1)
$exitCode = $LASTEXITCODE
$compilerOutput | ForEach-Object { Write-Host $_ }
if ($exitCode -ne 0) { throw "Connect IQ package export failed with exit code $exitCode." }
if (@($compilerOutput | Where-Object { "$_" -match '(?i)\bwarning\b' }).Count -gt 0) {
    throw 'Connect IQ package export emitted a compiler warning.'
}
if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
    throw 'Connect IQ package export reported success without creating the .iq package.'
}

$package = Get-Item -LiteralPath $packagePath
$sourceCommit = (& git -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to determine the source commit for package evidence.' }
$sdkVersion = ((@(& $compiler -v 2>&1) | ForEach-Object { "$_" }) -join ' ').Trim()

$evidence = [ordered]@{
    schemaVersion = 1
    package = $package.Name
    bytes = $package.Length
    sha256 = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash
    sourceCommit = $sourceCommit
    sdk = $sdkVersion
    developerKeySha256 = (Get-FileHash -LiteralPath $resolvedKey -Algorithm SHA256).Hash
    products = @(
        'fenix843mm',
        'fenix847mm',
        'fenix8solar47mm',
        'fenix8solar51mm',
        'vivoactive5',
        'vivoactive6'
    )
}
$evidencePath = Join-Path $resolvedOutput 'TreadmillRunnerCompanion-package.json'
$evidence | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $evidencePath -Encoding utf8

Write-Host "Signed Connect IQ package: $packagePath"
Write-Host "SHA-256: $($evidence.sha256)"
Write-Host "Evidence: $evidencePath"
