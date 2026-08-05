[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][ValidateLength(1, 4000)][string] $ReleaseNotes,
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string] $Repository = 'belgian-coder/treadmill-runner',
    [switch] $SkipValidation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if ($null -eq (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI (gh) is required.' }
    & gh auth status
    if ($LASTEXITCODE -ne 0) { throw 'Authenticate GitHub CLI with gh auth login before creating a release.' }
    $branch = (& git branch --show-current).Trim()
    if ($branch -ne 'main') { throw 'GitHub releases must be created from main.' }
    if (-not [string]::IsNullOrWhiteSpace((& git status --porcelain))) { throw 'Commit all intended changes before creating a release.' }
    $remoteUrl = (& git remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0 -or $remoteUrl -notmatch [Regex]::Escape($Repository)) {
        throw "origin must point to $Repository."
    }
    & git fetch origin main --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Could not refresh origin/main.' }
    $head = (& git rev-parse HEAD).Trim()
    $remoteHead = (& git rev-parse origin/main).Trim()
    if ($head -ne $remoteHead) { throw 'Local main must exactly match origin/main before release.' }
    & gh release view "v$Version" --repo $Repository *> $null
    if ($LASTEXITCODE -eq 0) { throw "GitHub release v$Version already exists." }

    if (-not $SkipValidation) {
        & (Join-Path $PSScriptRoot 'validate.ps1') -Configuration Release
        if ($LASTEXITCODE -ne 0) { throw 'Release validation failed.' }
        & (Join-Path $PSScriptRoot 'playwright.ps1') -Configuration Release
        if ($LASTEXITCODE -ne 0) { throw 'Browser validation failed.' }
    }

    $releaseRoot = Join-Path $projectRoot "artifacts\releases\$Version"
    $publishPath = Join-Path $releaseRoot 'publish'
    $feedPath = Join-Path $releaseRoot 'stable-feed'
    if (-not (Test-Path -LiteralPath $publishPath -PathType Container)) {
        & (Join-Path $PSScriptRoot 'publish-release.ps1') -Version $Version
        if ($LASTEXITCODE -ne 0) { throw 'Release publish failed.' }
    }
    $signerMetadataPath = Join-Path $projectRoot 'artifacts\release-signing\signer-metadata.json'
    if (-not (Test-Path -LiteralPath $signerMetadataPath -PathType Leaf)) {
        throw 'Release signer metadata is missing. Run initialize-release-signer.ps1 on the release workstation.'
    }
    $signer = Get-Content -LiteralPath $signerMetadataPath -Raw | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath (Join-Path $feedPath 'stable.manifest.json'))) {
        & (Join-Path $PSScriptRoot 'package-update.ps1') `
            -Version $Version `
            -PublishPath $publishPath `
            -FeedPath $feedPath `
            -SigningCertificateThumbprint ([string]$signer.thumbprint) `
            -ReleaseNotes $ReleaseNotes
        if ($LASTEXITCODE -ne 0) { throw 'Signed update packaging failed.' }
    }

    $publicCertificate = [System.IO.Path]::GetFullPath([string]$signer.publicCertificatePath)
    $installerBundle = Join-Path $releaseRoot "TreadmillRunner-$Version-Windows-x64.zip"
    if (-not (Test-Path -LiteralPath $installerBundle)) {
        & (Join-Path $PSScriptRoot 'new-installer-bundle.ps1') `
            -Version $Version `
            -PublishPath $publishPath `
            -PublicCertificatePath $publicCertificate `
            -OutputPath $installerBundle
        if ($LASTEXITCODE -ne 0) { throw 'Installer bundle creation failed.' }
    }

    $package = Join-Path $feedPath "treadmillrunner-$Version-win-x64.zip"
    $manifest = Join-Path $feedPath 'stable.manifest.json'
    $offlineBundle = Join-Path $feedPath "treadmillrunner-$Version-offline-update.zip"
    $assets = @($manifest, $package, $offlineBundle, $publicCertificate, $installerBundle)
    foreach ($asset in $assets) {
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) { throw "Release asset is missing: $asset" }
    }
    $checksums = Join-Path $releaseRoot 'SHA256SUMS.txt'
    $checksumLines = $assets | ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $([System.IO.Path]::GetFileName($_))"
    }
    [System.IO.File]::WriteAllLines($checksums, $checksumLines, [System.Text.UTF8Encoding]::new($false))
    $assets += $checksums

    & git tag -a "v$Version" -m "TreadmillRunner $Version"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the release tag.' }
    & git push origin "v$Version"
    if ($LASTEXITCODE -ne 0) { throw 'Could not push the release tag.' }

    & gh release create "v$Version" @assets `
        --repo $Repository `
        --title "TreadmillRunner $Version" `
        --notes $ReleaseNotes `
        --draft `
        --verify-tag `
        --fail-on-no-commits
    if ($LASTEXITCODE -ne 0) { throw 'Draft GitHub release creation failed; inspect any retained draft before retrying.' }
    $uploaded = @(& gh release view "v$Version" --repo $Repository --json assets --jq '.assets[].name')
    foreach ($asset in $assets) {
        if ($uploaded -notcontains [System.IO.Path]::GetFileName($asset)) {
            throw 'The draft release is incomplete; it has not been published.'
        }
    }
    & gh release edit "v$Version" --repo $Repository --draft=false --latest
    if ($LASTEXITCODE -ne 0) { throw 'The verified draft could not be published.' }
    Write-Host "GitHub release v$Version is published with signed update, offline, installer, certificate, and checksum assets."
}
finally {
    Pop-Location
}
