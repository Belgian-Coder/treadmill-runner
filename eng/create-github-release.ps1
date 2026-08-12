[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][ValidateLength(1, 4000)][string] $ReleaseNotes,
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string] $Repository = 'belgian-coder/treadmill-runner',
    [switch] $SkipValidation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Sha256Hex {
    param([Parameter(Mandatory)][string] $Path)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Get-ReleaseBuildId {
    param(
        [Parameter(Mandatory)][string] $ProjectRoot,
        [Parameter(Mandatory)][string] $Head
    )

    $contentLines = [System.Collections.Generic.List[string]]::new()
    $contentLines.Add($Head)
    $diffLines = @(& git -C $ProjectRoot diff --binary HEAD -- src Directory.Build.props)
    if ($LASTEXITCODE -ne 0) { throw 'The source fingerprint could not inspect local source changes.' }
    $contentLines.Add(($diffLines | ForEach-Object { [string]$_ }) -join "`n")
    $untrackedSource = @(& git -C $ProjectRoot ls-files --others --exclude-standard -- src Directory.Build.props) | Sort-Object
    if ($LASTEXITCODE -ne 0) { throw 'The source fingerprint could not inspect untracked source files.' }
    foreach ($relative in $untrackedSource) {
        $sourcePath = Join-Path $ProjectRoot $relative
        if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
            $contentLines.Add("$relative=$((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash)")
        }
    }
    $contentBytes = [System.Text.Encoding]::UTF8.GetBytes(($contentLines -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($contentBytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-RepositoryKey {
    param([Parameter(Mandatory)][string] $RemoteUrl)

    $value = $RemoteUrl.Trim().TrimEnd('/')
    $path = $null
    if ($value -match '^(?i)(?:https?|ssh|git)://(?:[^/@]+@)?github\.com/(?<path>[^/?#]+/[^/?#]+)$') {
        $path = $Matches.path
    }
    elseif ($value -match '^(?i)(?:[^/@]+@)?github\.com:(?<path>[^/?#]+/[^/?#]+)$') {
        $path = $Matches.path
    }
    else {
        return $null
    }
    if ($path.EndsWith('.git', [System.StringComparison]::OrdinalIgnoreCase)) {
        $path = $path.Substring(0, $path.Length - 4)
    }
    if ($path -notmatch '^[^/]+/[^/]+$') { return $null }
    return $path.ToLowerInvariant()
}

function Assert-OriginRepository {
    param([Parameter(Mandatory)][string] $Repository)

    $expectedKey = $Repository.ToLowerInvariant()
    $fetchUrls = @(& git remote get-url origin)
    $fetchExitCode = $LASTEXITCODE
    $pushUrls = @(& git remote get-url --push origin)
    $pushExitCode = $LASTEXITCODE
    if ($fetchExitCode -ne 0 -or $pushExitCode -ne 0 -or $fetchUrls.Count -ne 1 -or $pushUrls.Count -lt 1) {
        throw "origin must have exactly one valid fetch URL and at least one valid push URL for $Repository."
    }
    foreach ($url in @($fetchUrls + $pushUrls)) {
        $key = Get-RepositoryKey -RemoteUrl ([string]$url)
        if ($null -eq $key -or $key -ne $expectedKey) {
            throw "origin fetch and push URLs must exactly target $Repository."
        }
    }
}

function Get-GhReleaseView {
    param(
        [Parameter(Mandatory)][string] $Tag,
        [Parameter(Mandatory)][string] $Repository
    )

    $errorPath = [System.IO.Path]::GetTempFileName()
    try {
        $jsonLines = @(& gh release view $Tag --repo $Repository --json isDraft,body 2> $errorPath)
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0) {
            return [pscustomobject]@{
                Found = $true
                Json = ($jsonLines -join "`n")
            }
        }
        $errorText = if (Test-Path -LiteralPath $errorPath -PathType Leaf) {
            Get-Content -LiteralPath $errorPath -Raw
        }
        else {
            ''
        }
        if ($errorText -match '(?i)(?:\b404\b|not found)') {
            return [pscustomobject]@{
                Found = $false
                Json = $null
            }
        }
        throw "Could not inspect GitHub release ${Tag}: $($errorText.Trim())"
    }
    finally {
        if (Test-Path -LiteralPath $errorPath -PathType Leaf) {
            Remove-Item -LiteralPath $errorPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-MaxPublishedReleaseVersion {
    param([Parameter(Mandatory)][string] $Repository)

    $publishedTags = @(& gh api --paginate "repos/$Repository/releases?per_page=100" --jq '.[] | select(.draft == false) | .tag_name')
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the published release versions.' }
    $publishedVersions = @($publishedTags | ForEach-Object {
        if ($_ -match '^v(?<version>\d+\.\d+\.\d+)$') { [version]$Matches.version }
    })
    if ($publishedVersions.Count -eq 0) { return $null }
    return ($publishedVersions | Sort-Object -Descending | Select-Object -First 1)
}

function Assert-VersionIsNewer {
    param(
        [Parameter(Mandatory)][string] $Version,
        [Parameter(Mandatory)][string] $Repository
    )

    $maximum = Get-MaxPublishedReleaseVersion -Repository $Repository
    if ($null -ne $maximum -and [version]$Version -le [version]$maximum) {
        throw "Version $Version must be newer than every published release."
    }
}

function Get-ZipEntryBytes {
    param(
        [Parameter(Mandatory)]$Archive,
        [Parameter(Mandatory)][string] $Name
    )

    $entry = $Archive.GetEntry($Name)
    if ($null -eq $entry) { throw "ZIP is missing required entry: $Name" }
    $input = $entry.Open()
    $memory = [System.IO.MemoryStream]::new()
    try {
        $input.CopyTo($memory)
        return ,$memory.ToArray()
    }
    finally {
        $input.Dispose()
        $memory.Dispose()
    }
}

function Get-ZipEntryText {
    param(
        [Parameter(Mandatory)]$Archive,
        [Parameter(Mandatory)][string] $Name
    )

    return [System.Text.Encoding]::UTF8.GetString([byte[]](Get-ZipEntryBytes -Archive $Archive -Name $Name))
}

function Assert-ByteArraysEqual {
    param(
        [Parameter(Mandatory)][byte[]] $Expected,
        [Parameter(Mandatory)][byte[]] $Actual,
        [Parameter(Mandatory)][string] $Description
    )

    if ($Expected.Length -ne $Actual.Length) { throw "$Description does not match its source." }
    for ($index = 0; $index -lt $Expected.Length; $index++) {
        if ($Expected[$index] -ne $Actual[$index]) { throw "$Description does not match its source." }
    }
}

function Assert-BuildMetadata {
    param(
        [Parameter(Mandatory)][string] $Path,
        [Parameter(Mandatory)][string] $Version,
        [Parameter(Mandatory)][string] $Head,
        [Parameter(Mandatory)][string] $ExpectedBuildId
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Release publish is incomplete: build metadata is missing: $Path" }
    $metadata = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ([string]$metadata.version -ne $Version -or
        [string]$metadata.sourceRevision -ne $Head -or
        [string]$metadata.buildId -ne $ExpectedBuildId) {
        throw 'Existing release output does not match the validated version and source fingerprint.'
    }
    return $metadata
}

function Assert-SignedManifest {
    param(
        [Parameter(Mandatory)][string] $ManifestPath,
        [Parameter(Mandatory)][string] $PackagePath,
        [Parameter(Mandatory)][string] $Version,
        [Parameter(Mandatory)][string] $ReleaseNotes,
        [Parameter(Mandatory)][string] $PublicCertificatePath,
        [Parameter(Mandatory)][string] $SignerThumbprint
    )

    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $packageFileName = [System.IO.Path]::GetFileName($PackagePath)
    $actualPackageHash = Get-Sha256Hex -Path $PackagePath
    if ([string]$manifest.schemaVersion -ne '1' -or
        [string]$manifest.version -ne $Version -or
        [string]$manifest.channel -ne 'stable' -or
        [string]$manifest.packageFileName -ne $packageFileName -or
        ([string]$manifest.packageSha256).ToLowerInvariant() -ne $actualPackageHash -or
        [string]$manifest.releaseNotes -ne $ReleaseNotes) {
        throw 'The signed stable manifest does not match the requested version, notes, package, or channel.'
    }

    $normalizedNotes = ([string]$manifest.releaseNotes).Replace("`r`n", "`n").Replace("`r", "`n")
    $payload = @(
        '1',
        [string]$manifest.version,
        [string]$manifest.channel,
        [string]$manifest.packageFileName,
        ([string]$manifest.packageSha256).ToUpperInvariant(),
        [string]$manifest.minimumDatabaseSchemaVersion,
        [string]$manifest.maximumDatabaseSchemaVersion,
        $normalizedNotes
    ) -join "`n"
    $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($PublicCertificatePath)
    if ([string]$certificate.Thumbprint -ne $SignerThumbprint.ToUpperInvariant()) {
        $certificate.Dispose()
        throw 'The public release certificate does not match signer metadata.'
    }
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
    try {
        $signatureBytes = [Convert]::FromBase64String([string]$manifest.signature)
        if ($null -eq $rsa -or -not $rsa.VerifyData(
            [System.Text.Encoding]::UTF8.GetBytes($payload),
            $signatureBytes,
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)) {
            throw 'The stable manifest signature is invalid for the configured release certificate.'
        }
    }
    finally {
        if ($null -ne $rsa) { $rsa.Dispose() }
        $certificate.Dispose()
    }
    return $manifest
}

function Assert-ReleaseArtifacts {
    param(
        [Parameter(Mandatory)][string] $ReleaseRoot,
        [Parameter(Mandatory)][string] $Version,
        [Parameter(Mandatory)][string] $Head,
        [Parameter(Mandatory)][string] $ExpectedBuildId,
        [Parameter(Mandatory)][string] $ReleaseNotes,
        [Parameter(Mandatory)][string] $PublicCertificatePath,
        [Parameter(Mandatory)][string] $SignerThumbprint
    )

    $publishPath = Join-Path $ReleaseRoot 'publish'
    $feedPath = Join-Path $ReleaseRoot 'stable-feed'
    $package = Join-Path $feedPath "treadmillrunner-$Version-win-x64.zip"
    $manifest = Join-Path $feedPath 'stable.manifest.json'
    $offlineBundle = Join-Path $feedPath "treadmillrunner-$Version-offline-update.zip"
    $installerBundle = Join-Path $ReleaseRoot "TreadmillRunner-$Version-Windows-x64.zip"
    $checksums = Join-Path $ReleaseRoot 'SHA256SUMS.txt'
    $expectedRootEntries = @('publish', 'stable-feed', [System.IO.Path]::GetFileName($installerBundle), 'SHA256SUMS.txt')
    $actualRootEntries = @(Get-ChildItem -LiteralPath $ReleaseRoot -Force | ForEach-Object { $_.Name })
    if ($actualRootEntries.Count -ne $expectedRootEntries.Count -or
        @($actualRootEntries | Where-Object { $expectedRootEntries -notcontains $_ }).Count -ne 0) {
        throw 'Release output is partial or contains unexpected top-level files.'
    }
    $expectedFeedEntries = @([System.IO.Path]::GetFileName($package), 'stable.manifest.json', [System.IO.Path]::GetFileName($offlineBundle))
    if (Test-Path -LiteralPath $feedPath -PathType Container) {
        $actualFeedEntries = @(Get-ChildItem -LiteralPath $feedPath -Force | ForEach-Object { $_.Name })
        if ($actualFeedEntries.Count -ne $expectedFeedEntries.Count -or
            @($actualFeedEntries | Where-Object { $expectedFeedEntries -notcontains $_ }).Count -ne 0) {
            throw 'Stable feed output is partial or contains unexpected files.'
        }
    }
    foreach ($path in @($publishPath, $feedPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "Release output is partial or missing: $path" }
    }
    foreach ($path in @($package, $manifest, $offlineBundle, $PublicCertificatePath, $installerBundle, $checksums)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Release asset is missing or partial: $path" }
    }

    $metadataPath = Join-Path $publishPath 'build-metadata.json'
    $metadata = Assert-BuildMetadata -Path $metadataPath -Version $Version -Head $Head -ExpectedBuildId $ExpectedBuildId
    $signedManifest = Assert-SignedManifest `
        -ManifestPath $manifest `
        -PackagePath $package `
        -Version $Version `
        -ReleaseNotes $ReleaseNotes `
        -PublicCertificatePath $PublicCertificatePath `
        -SignerThumbprint $SignerThumbprint

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $packageArchive = [System.IO.Compression.ZipFile]::OpenRead($package)
    try {
        $packageMetadata = Get-ZipEntryText -Archive $packageArchive -Name 'build-metadata.json' | ConvertFrom-Json
        if ([string]$packageMetadata.version -ne [string]$metadata.version -or
            [string]$packageMetadata.sourceRevision -ne [string]$metadata.sourceRevision -or
            [string]$packageMetadata.buildId -ne [string]$metadata.buildId) {
            throw 'The update package build provenance does not match the published output.'
        }
    }
    finally { $packageArchive.Dispose() }

    $offlineArchive = [System.IO.Compression.ZipFile]::OpenRead($offlineBundle)
    try {
        $offlineNames = @($offlineArchive.Entries | ForEach-Object { $_.FullName })
        $expectedOfflineNames = @('stable.manifest.json', [System.IO.Path]::GetFileName($package))
        if ($offlineNames.Count -ne 2 -or
            @($offlineNames | Where-Object { $expectedOfflineNames -notcontains $_ }).Count -ne 0) {
            throw 'The offline update bundle must contain exactly the signed manifest and its package.'
        }
        foreach ($expectedName in $expectedOfflineNames) {
            if (@($offlineNames | Where-Object { $_ -eq $expectedName }).Count -ne 1) {
                throw 'The offline update bundle must contain each expected entry exactly once.'
            }
        }
        Assert-ByteArraysEqual `
            -Expected ([System.IO.File]::ReadAllBytes($manifest)) `
            -Actual ([byte[]](Get-ZipEntryBytes -Archive $offlineArchive -Name 'stable.manifest.json')) `
            -Description 'The offline manifest'
        Assert-ByteArraysEqual `
            -Expected ([System.IO.File]::ReadAllBytes($package)) `
            -Actual ([byte[]](Get-ZipEntryBytes -Archive $offlineArchive -Name ([System.IO.Path]::GetFileName($package)))) `
            -Description 'The offline package'
    }
    finally { $offlineArchive.Dispose() }

    $installerArchive = [System.IO.Compression.ZipFile]::OpenRead($installerBundle)
    try {
        $installerRelease = Get-ZipEntryText -Archive $installerArchive -Name 'release.json' | ConvertFrom-Json
        $installerMetadata = Get-ZipEntryText -Archive $installerArchive -Name 'app/build-metadata.json' | ConvertFrom-Json
        if ([string]$installerRelease.version -ne $Version -or
            [string]$installerMetadata.version -ne [string]$metadata.version -or
            [string]$installerMetadata.sourceRevision -ne [string]$metadata.sourceRevision -or
            [string]$installerMetadata.buildId -ne [string]$metadata.buildId) {
            throw 'The installer bundle build provenance does not match the published output.'
        }
        Assert-ByteArraysEqual `
            -Expected ([System.IO.File]::ReadAllBytes($PublicCertificatePath)) `
            -Actual ([byte[]](Get-ZipEntryBytes -Archive $installerArchive -Name 'treadmillrunner-release-signing.cer')) `
            -Description 'The installer trust certificate'
    }
    finally { $installerArchive.Dispose() }

    $assetPaths = @($manifest, $package, $offlineBundle, $PublicCertificatePath, $installerBundle)
    $expectedChecksumLines = @($assetPaths | ForEach-Object {
        "$(Get-Sha256Hex -Path $_)  $([System.IO.Path]::GetFileName($_))"
    })
    $actualChecksumLines = @(Get-Content -LiteralPath $checksums)
    if ($actualChecksumLines.Count -ne $expectedChecksumLines.Count) {
        throw 'SHA256SUMS.txt is partial or contains unexpected assets.'
    }
    for ($index = 0; $index -lt $expectedChecksumLines.Count; $index++) {
        if ($actualChecksumLines[$index] -ne $expectedChecksumLines[$index]) {
            throw 'SHA256SUMS.txt does not match the verified release assets.'
        }
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    if ($null -eq (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI (gh) is required.' }
    & gh auth status
    if ($LASTEXITCODE -ne 0) { throw 'Authenticate GitHub CLI with gh auth login before creating a release.' }
    $branch = (& git branch --show-current).Trim()
    if ($branch -ne 'main') { throw 'GitHub releases must be created from main.' }
    if (-not [string]::IsNullOrWhiteSpace((& git status --porcelain))) { throw 'Commit all intended changes before creating a release.' }
    Assert-OriginRepository -Repository $Repository
    & git fetch origin main --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Could not refresh origin/main.' }
    $head = (& git rev-parse HEAD).Trim()
    $remoteHead = (& git rev-parse origin/main).Trim()
    if ($head -ne $remoteHead) { throw 'Local main must exactly match origin/main before release.' }
    $tag = "v$Version"
    $existingReleaseView = Get-GhReleaseView -Tag $tag -Repository $Repository
    $existingDraft = $false
    if ($existingReleaseView.Found) {
        $existingRelease = $existingReleaseView.Json | ConvertFrom-Json
        if (-not [bool]$existingRelease.isDraft) { throw "GitHub release $tag is already published." }
        $existingNotes = ([string]$existingRelease.body).Replace("`r`n", "`n").TrimEnd()
        $requestedNotes = $ReleaseNotes.Replace("`r`n", "`n").Replace("`r", "`n").TrimEnd()
        if ($existingNotes -ne $requestedNotes) {
            throw 'ReleaseNotes must exactly match the existing draft release when resuming.'
        }
        $existingDraft = $true
    }

    if ($SkipValidation -and -not $existingDraft) {
        throw 'SkipValidation is allowed only when resuming an existing verified draft release.'
    }

    Assert-VersionIsNewer -Version $Version -Repository $Repository

    if (-not $SkipValidation) {
        $previousShowcaseMode = $env:TREADMILLRUNNER_UPDATE_SHOWCASE
        try {
            $env:TREADMILLRUNNER_UPDATE_SHOWCASE = '0'
            & (Join-Path $PSScriptRoot 'validate.ps1') -Configuration Release
            if ($LASTEXITCODE -ne 0) { throw 'Release validation failed.' }
            & (Join-Path $PSScriptRoot 'playwright.ps1') -Configuration Release -TimeoutMinutes 7
            if ($LASTEXITCODE -ne 0) { throw 'Browser validation failed.' }
        }
        finally {
            if ($null -eq $previousShowcaseMode) { Remove-Item Env:TREADMILLRUNNER_UPDATE_SHOWCASE -ErrorAction SilentlyContinue }
            else { $env:TREADMILLRUNNER_UPDATE_SHOWCASE = $previousShowcaseMode }
        }
    }

    $validatedHead = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $validatedHead -ne $head) {
        throw 'The checked-out commit changed during release validation.'
    }
    if (-not [string]::IsNullOrWhiteSpace((& git status --porcelain))) {
        throw 'Release validation changed tracked or untracked files. Review and commit deterministic evidence, then rerun the release.'
    }
    & git fetch origin main --quiet
    if ($LASTEXITCODE -ne 0 -or (& git rev-parse origin/main).Trim() -ne $head) {
        throw 'origin/main changed during release validation. Refresh and revalidate before publishing.'
    }

    $signerMetadataPath = Join-Path $projectRoot 'artifacts\release-signing\signer-metadata.json'
    if (-not (Test-Path -LiteralPath $signerMetadataPath -PathType Leaf)) {
        throw 'Release signer metadata is missing. Run initialize-release-signer.ps1 on the release workstation.'
    }
    $signer = Get-Content -LiteralPath $signerMetadataPath -Raw | ConvertFrom-Json
    $publicCertificate = [System.IO.Path]::GetFullPath([string]$signer.publicCertificatePath)
    if (-not (Test-Path -LiteralPath $publicCertificate -PathType Leaf)) {
        throw "The configured public release certificate is missing: $publicCertificate"
    }
    if ([string]::IsNullOrWhiteSpace([string]$signer.thumbprint)) {
        throw 'Release signer metadata has no certificate thumbprint.'
    }
    $expectedBuildId = Get-ReleaseBuildId -ProjectRoot $projectRoot -Head $head
    $releaseBasePath = Join-Path $projectRoot 'artifacts\releases'
    New-Item -ItemType Directory -Path $releaseBasePath -Force | Out-Null
    $releaseRoot = Join-Path $releaseBasePath $Version
    $stagingRoot = $null
    try {
        $partialStaging = @(Get-ChildItem -LiteralPath $releaseBasePath -Directory -Filter ".staging-$Version-*" -ErrorAction SilentlyContinue)
        if ($partialStaging.Count -gt 0) {
            throw "An interrupted staged release exists for $Version. Remove the explicit staging directory before retrying: $($partialStaging[0].FullName)"
        }
        if (Test-Path -LiteralPath $releaseRoot) {
            if (-not (Test-Path -LiteralPath $releaseRoot -PathType Container)) {
                throw "Release output path is not a directory: $releaseRoot"
            }
            Assert-ReleaseArtifacts `
                -ReleaseRoot $releaseRoot `
                -Version $Version `
                -Head $head `
                -ExpectedBuildId $expectedBuildId `
                -ReleaseNotes $ReleaseNotes `
                -PublicCertificatePath $publicCertificate `
                -SignerThumbprint ([string]$signer.thumbprint)
        }
        else {
            $stagingRoot = Join-Path $releaseBasePath ".staging-$Version-$([Guid]::NewGuid().ToString('N'))"
            $stagedReleaseRoot = Join-Path $stagingRoot $Version
            & (Join-Path $PSScriptRoot 'publish-release.ps1') -Version $Version -OutputRoot $stagingRoot
            if ($LASTEXITCODE -ne 0) { throw 'Release publish failed; staged output was discarded.' }
            $stagedPublishPath = Join-Path $stagedReleaseRoot 'publish'
            $stagedFeedPath = Join-Path $stagedReleaseRoot 'stable-feed'
            & (Join-Path $PSScriptRoot 'package-update.ps1') `
                -Version $Version `
                -PublishPath $stagedPublishPath `
                -FeedPath $stagedFeedPath `
                -SigningCertificateThumbprint ([string]$signer.thumbprint) `
                -ReleaseNotes $ReleaseNotes
            if ($LASTEXITCODE -ne 0) { throw 'Signed update packaging failed; staged output was discarded.' }
            $stagedInstaller = Join-Path $stagedReleaseRoot "TreadmillRunner-$Version-Windows-x64.zip"
            & (Join-Path $PSScriptRoot 'new-installer-bundle.ps1') `
                -Version $Version `
                -PublishPath $stagedPublishPath `
                -PublicCertificatePath $publicCertificate `
                -OutputPath $stagedInstaller
            if ($LASTEXITCODE -ne 0) { throw 'Installer bundle creation failed; staged output was discarded.' }

            $stagedManifest = Join-Path $stagedFeedPath 'stable.manifest.json'
            $stagedPackage = Join-Path $stagedFeedPath "treadmillrunner-$Version-win-x64.zip"
            $stagedOffline = Join-Path $stagedFeedPath "treadmillrunner-$Version-offline-update.zip"
            $stagedAssets = @($stagedManifest, $stagedPackage, $stagedOffline, $publicCertificate, $stagedInstaller)
            $stagedChecksums = Join-Path $stagedReleaseRoot 'SHA256SUMS.txt'
            $stagedChecksumLines = @($stagedAssets | ForEach-Object {
                "$(Get-Sha256Hex -Path $_)  $([System.IO.Path]::GetFileName($_))"
            })
            [System.IO.File]::WriteAllLines($stagedChecksums, $stagedChecksumLines, [System.Text.UTF8Encoding]::new($false))
            Assert-ReleaseArtifacts `
                -ReleaseRoot $stagedReleaseRoot `
                -Version $Version `
                -Head $head `
                -ExpectedBuildId $expectedBuildId `
                -ReleaseNotes $ReleaseNotes `
                -PublicCertificatePath $publicCertificate `
                -SignerThumbprint ([string]$signer.thumbprint)
            if (Test-Path -LiteralPath $releaseRoot) {
                throw "Release output appeared while staging $Version; no tag was created."
            }
            Move-Item -LiteralPath $stagedReleaseRoot -Destination $releaseRoot -ErrorAction Stop
        }
    }
    finally {
        if ($null -ne $stagingRoot -and (Test-Path -LiteralPath $stagingRoot)) {
            Remove-Item -LiteralPath $stagingRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    $publishPath = Join-Path $releaseRoot 'publish'
    $feedPath = Join-Path $releaseRoot 'stable-feed'
    $package = Join-Path $feedPath "treadmillrunner-$Version-win-x64.zip"
    $manifest = Join-Path $feedPath 'stable.manifest.json'
    $offlineBundle = Join-Path $feedPath "treadmillrunner-$Version-offline-update.zip"
    $installerBundle = Join-Path $releaseRoot "TreadmillRunner-$Version-Windows-x64.zip"
    $checksums = Join-Path $releaseRoot 'SHA256SUMS.txt'
    $assets = @($manifest, $package, $offlineBundle, $publicCertificate, $installerBundle, $checksums)

    $finalHead = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $finalHead -ne $head -or
        -not [string]::IsNullOrWhiteSpace((& git status --porcelain))) {
        throw 'The validated source changed while release assets were being prepared.'
    }
    & git fetch origin main --quiet
    if ($LASTEXITCODE -ne 0 -or (& git rev-parse origin/main).Trim() -ne $head) {
        throw 'origin/main changed while release assets were being prepared.'
    }
    Assert-OriginRepository -Repository $Repository
    Assert-VersionIsNewer -Version $Version -Repository $Repository

    & git show-ref --verify --quiet "refs/tags/$tag"
    if ($LASTEXITCODE -ne 0) {
        # Recover a tag pushed by an interrupted run or another clean checkout.
        & git fetch origin "refs/tags/${tag}:refs/tags/${tag}" --quiet 2> $null
        & git show-ref --verify --quiet "refs/tags/$tag"
    }
    if ($LASTEXITCODE -eq 0) {
        $tagObjectType = (& git cat-file -t "$tag").Trim()
        if ($LASTEXITCODE -ne 0 -or $tagObjectType -ne 'tag') {
            throw "Existing tag $tag is not an annotated tag. Tags are never moved."
        }
        $tagCommit = (& git rev-list -n 1 "$tag^{commit}").Trim()
        if ($LASTEXITCODE -ne 0 -or $tagCommit -ne $head) {
            throw "Existing local tag $tag does not identify the current main commit. Tags are never moved."
        }
    }
    else {
        & git tag -a $tag -m "TreadmillRunner $Version"
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the annotated release tag.' }
    }
    & git push origin "refs/tags/${tag}:refs/tags/${tag}"
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
    $expectedAssetNames = @($assets | ForEach-Object { [System.IO.Path]::GetFileName($_) })
    foreach ($asset in $assets) {
        if ($uploaded -notcontains [System.IO.Path]::GetFileName($asset)) {
            throw 'The draft release is incomplete; it has not been published.'
        }
    }
    $unexpectedAssets = @($uploaded | Where-Object { $expectedAssetNames -notcontains $_ })
    if ($unexpectedAssets.Count -gt 0 -or $uploaded.Count -ne $expectedAssetNames.Count) {
        throw "The draft release contains unexpected assets and remains unpublished: $($unexpectedAssets -join ', ')."
    }
    & gh release edit $tag --repo $Repository --draft=false --latest
    if ($LASTEXITCODE -ne 0) { throw 'The verified draft could not be published.' }
    Write-Host "GitHub release $tag is published with signed update, offline, installer, certificate, and checksum assets."
}
finally {
    Pop-Location
}
