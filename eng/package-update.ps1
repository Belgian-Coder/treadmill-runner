[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][string] $PublishPath,
    [Parameter(Mandatory)][string] $FeedPath,
    [Parameter(Mandatory)][string] $SigningCertificateThumbprint,
    [Parameter(Mandatory)][ValidateLength(1, 4000)][string] $ReleaseNotes,
    [ValidatePattern('^[a-z0-9][a-z0-9._-]{0,31}$')][string] $Channel = 'stable',
    [ValidateRange(0, 10000)][int] $MinimumDatabaseSchemaVersion = 0,
    [ValidateRange(0, 10000)][int] $MaximumDatabaseSchemaVersion = 100
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Get-ArchiveRelativePath {
    param([Parameter(Mandatory)][string] $Root, [Parameter(Mandatory)][string] $Path)
    $normalizedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    $normalizedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $normalizedPath.StartsWith($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'A package input escaped the publish root.'
    }
    return $normalizedPath.Substring($normalizedRoot.Length).Replace('\', '/')
}
function Get-Sha256Hex {
    param([Parameter(Mandatory)][string] $Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '') }
    finally { $stream.Dispose(); $sha.Dispose() }
}
$resolvedPublish = [System.IO.Path]::GetFullPath($PublishPath)
$resolvedFeed = [System.IO.Path]::GetFullPath($FeedPath)
if (-not (Test-Path -LiteralPath $resolvedPublish -PathType Container)) { throw 'PublishPath is missing.' }
if ($resolvedFeed.StartsWith($resolvedPublish + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The update feed cannot be inside the publish input.'
}
foreach ($required in @('TreadmillRunner.Gateway.exe', 'TreadmillRunner.Migrations.exe', 'Updates\update-helper.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedPublish $required) -PathType Leaf)) {
        throw "The publish input is missing $required."
    }
}
& (Join-Path $PSScriptRoot 'test-garmin-adapter-runtime.ps1') -PublishPath $resolvedPublish
if ($LASTEXITCODE -ne 0) { throw 'Bundled Garmin adapter validation failed.' }
$gatewayVersion = [System.Reflection.AssemblyName]::GetAssemblyName(
    (Join-Path $resolvedPublish 'TreadmillRunner.Gateway.dll')).Version
if ($gatewayVersion.Major -ne ([Version]$Version).Major -or
    $gatewayVersion.Minor -ne ([Version]$Version).Minor -or
    $gatewayVersion.Build -ne ([Version]$Version).Build) {
    throw "Package version $Version does not match assembly version $gatewayVersion."
}

$certificate = Get-Item -LiteralPath ("Cert:\CurrentUser\My\$SigningCertificateThumbprint") -ErrorAction Stop
if (-not $certificate.HasPrivateKey) { throw 'The signing certificate has no private key.' }
New-Item -ItemType Directory -Path $resolvedFeed -Force | Out-Null
$packageFileName = "treadmillrunner-$Version-win-x64.zip"
$packagePath = Join-Path $resolvedFeed $packageFileName
$manifestPath = Join-Path $resolvedFeed "$Channel.manifest.json"
$offlineBundlePath = Join-Path $resolvedFeed "treadmillrunner-$Version-offline-update.zip"
if (Test-Path -LiteralPath $packagePath) { throw 'The target update package already exists.' }
if (Test-Path -LiteralPath $offlineBundlePath) { throw 'The target offline update bundle already exists.' }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archiveStream = [System.IO.File]::Open($packagePath, [System.IO.FileMode]::CreateNew)
try {
    $archive = [System.IO.Compression.ZipArchive]::new(
        $archiveStream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
    try {
        Get-ChildItem -LiteralPath $resolvedPublish -File -Recurse |
            Sort-Object FullName |
            ForEach-Object {
                $relative = Get-ArchiveRelativePath -Root $resolvedPublish -Path $_.FullName
                $entry = $archive.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = [DateTimeOffset]::new(1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
                $input = [System.IO.File]::OpenRead($_.FullName)
                $output = $entry.Open()
                try { $input.CopyTo($output) }
                finally { $output.Dispose(); $input.Dispose() }
            }
    }
    finally { $archive.Dispose() }
}
catch {
    $archiveStream.Dispose()
    Remove-Item -LiteralPath $packagePath -Force -ErrorAction SilentlyContinue
    throw
}
finally { $archiveStream.Dispose() }

$hash = Get-Sha256Hex -Path $packagePath
$unsigned = [ordered]@{
    schemaVersion = 1
    version = $Version
    channel = $Channel
    packageFileName = $packageFileName
    packageSha256 = $hash
    minimumDatabaseSchemaVersion = $MinimumDatabaseSchemaVersion
    maximumDatabaseSchemaVersion = $MaximumDatabaseSchemaVersion
    releaseNotes = $ReleaseNotes
    signature = ''
}
$normalizedNotes = $ReleaseNotes.Replace("`r`n", "`n").Replace("`r", "`n")
$payload = @('1', $Version, $Channel, $packageFileName, $hash.ToUpperInvariant(),
    [string]$MinimumDatabaseSchemaVersion, [string]$MaximumDatabaseSchemaVersion, $normalizedNotes) -join "`n"
$rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($certificate)
try {
    $signature = $rsa.SignData(
        [System.Text.Encoding]::UTF8.GetBytes($payload),
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $unsigned.signature = [Convert]::ToBase64String($signature)
}
finally { if ($null -ne $rsa) { $rsa.Dispose() } }
$temporaryManifest = "$manifestPath.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    [System.IO.File]::WriteAllText(
        $temporaryManifest,
        ($unsigned | ConvertTo-Json -Compress),
        [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryManifest -Destination $manifestPath -Force
}
finally {
    if (Test-Path -LiteralPath $temporaryManifest) { Remove-Item -LiteralPath $temporaryManifest -Force }
}

$bundleStream = [System.IO.File]::Open($offlineBundlePath, [System.IO.FileMode]::CreateNew)
try {
    $bundle = [System.IO.Compression.ZipArchive]::new(
        $bundleStream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
    try {
        foreach ($item in @(
            @{ Path = $manifestPath; Name = "$Channel.manifest.json"; Compression = [System.IO.Compression.CompressionLevel]::Optimal },
            @{ Path = $packagePath; Name = $packageFileName; Compression = [System.IO.Compression.CompressionLevel]::NoCompression }
        )) {
            $entry = $bundle.CreateEntry($item.Name, $item.Compression)
            $entry.LastWriteTime = [DateTimeOffset]::new(1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
            $input = [System.IO.File]::OpenRead($item.Path)
            $output = $entry.Open()
            try { $input.CopyTo($output) }
            finally { $output.Dispose(); $input.Dispose() }
        }
    }
    finally { $bundle.Dispose() }
}
catch {
    $bundleStream.Dispose()
    Remove-Item -LiteralPath $offlineBundlePath -Force -ErrorAction SilentlyContinue
    throw
}
finally { $bundleStream.Dispose() }

Write-Host "Signed update $Version and offline bundle written to $resolvedFeed"
