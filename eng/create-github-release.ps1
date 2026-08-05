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
    $tag = "v$Version"
    $existingReleaseJson = & gh release view $tag --repo $Repository --json isDraft 2> $null
    $existingDraft = $false
    if ($LASTEXITCODE -eq 0) {
        $existingRelease = $existingReleaseJson | ConvertFrom-Json
        if (-not [bool]$existingRelease.isDraft) { throw "GitHub release $tag is already published." }
        $existingDraft = $true
    }

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
    $signedManifest = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    if ([string]$signedManifest.version -ne $Version) {
        throw 'The signed manifest version does not match the requested release version.'
    }
    if ([string]$signedManifest.releaseNotes -ne $ReleaseNotes) {
        throw 'ReleaseNotes must exactly match the already signed manifest when resuming a release.'
    }
    $checksums = Join-Path $releaseRoot 'SHA256SUMS.txt'
    $checksumLines = $assets | ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $([System.IO.Path]::GetFileName($_))"
    }
    [System.IO.File]::WriteAllLines($checksums, $checksumLines, [System.Text.UTF8Encoding]::new($false))
    $assets += $checksums

    & git show-ref --verify --quiet "refs/tags/$tag"
    if ($LASTEXITCODE -ne 0) {
        # Recover a tag pushed by an interrupted run or another clean checkout.
        & git fetch origin "refs/tags/$tag:refs/tags/$tag" --quiet 2> $null
        & git show-ref --verify --quiet "refs/tags/$tag"
    }
    if ($LASTEXITCODE -eq 0) {
        $tagCommit = (& git rev-list -n 1 "$tag^{commit}").Trim()
        if ($LASTEXITCODE -ne 0 -or $tagCommit -ne $head) {
            throw "Existing local tag $tag does not identify the current main commit. Tags are never moved."
        }
    }
    else {
        & git tag -a $tag -m "TreadmillRunner $Version"
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the annotated release tag.' }
    }
    & git push origin "refs/tags/$tag:refs/tags/$tag"
    if ($LASTEXITCODE -ne 0) { throw 'Could not push the release tag; a conflicting remote tag is never overwritten.' }

    if ($existingDraft) {
        & gh release edit $tag --repo $Repository --title "TreadmillRunner $Version" --notes $ReleaseNotes
        if ($LASTEXITCODE -ne 0) { throw 'The existing draft release metadata could not be refreshed.' }
        & gh release upload $tag @assets --repo $Repository --clobber
        if ($LASTEXITCODE -ne 0) { throw 'Draft release asset upload failed; the draft remains unpublished for a safe retry.' }
    }
    else {
        & gh release create $tag @assets `
            --repo $Repository `
            --title "TreadmillRunner $Version" `
            --notes $ReleaseNotes `
            --draft `
            --verify-tag `
            --fail-on-no-commits
        if ($LASTEXITCODE -ne 0) { throw 'Draft GitHub release creation failed; inspect any retained draft before retrying.' }
    }
    $uploaded = @(& gh release view $tag --repo $Repository --json assets --jq '.assets[].name')
    foreach ($asset in $assets) {
        if ($uploaded -notcontains [System.IO.Path]::GetFileName($asset)) {
            throw 'The draft release is incomplete; it has not been published.'
        }
    }
    & gh release edit $tag --repo $Repository --draft=false --latest
    if ($LASTEXITCODE -ne 0) { throw 'The verified draft could not be published.' }
    Write-Host "GitHub release $tag is published with signed update, offline, installer, certificate, and checksum assets."
}
finally {
    Pop-Location
}
