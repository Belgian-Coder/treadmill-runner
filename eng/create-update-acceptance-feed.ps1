[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $PublishPath,
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $GoodVersion,
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $BrokenVersion,
    [Parameter(Mandatory)][string] $FeedPath,
    [Parameter(Mandatory)][string] $PublicCertificatePath
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
$resolvedCertificate = [System.IO.Path]::GetFullPath($PublicCertificatePath)
if (-not (Test-Path -LiteralPath $resolvedPublish -PathType Container)) { throw 'PublishPath is missing.' }
if ([Version]$BrokenVersion -le [Version]$GoodVersion) { throw 'BrokenVersion must be newer than GoodVersion.' }
New-Item -ItemType Directory -Path $resolvedFeed -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedCertificate) -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$rsa = [System.Security.Cryptography.RSA]::Create(3072)
$request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
    'CN=TreadmillRunner acceptance-only update signer',
    $rsa,
    [System.Security.Cryptography.HashAlgorithmName]::SHA256,
    [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$certificate = $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5), [DateTimeOffset]::UtcNow.AddDays(2))
try {
    [System.IO.File]::WriteAllBytes(
        $resolvedCertificate,
        $certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))

    foreach ($release in @(
        [pscustomobject]@{ Version = $GoodVersion; Broken = $false; Notes = 'Acceptance release B: signed update promotion.' },
        [pscustomobject]@{ Version = $BrokenVersion; Broken = $true; Notes = 'Acceptance release C: intentionally broken rollback fixture.' })) {
        $packageFileName = "treadmillrunner-$($release.Version)-win-x64.zip"
        $packagePath = Join-Path $resolvedFeed $packageFileName
        if (Test-Path -LiteralPath $packagePath) { throw "Acceptance package already exists: $packagePath" }
        $file = [System.IO.File]::Open($packagePath, [System.IO.FileMode]::CreateNew)
        try {
            $archive = [System.IO.Compression.ZipArchive]::new($file, [System.IO.Compression.ZipArchiveMode]::Create, $false)
            try {
                Get-ChildItem -LiteralPath $resolvedPublish -File -Recurse |
                    Where-Object { -not ($release.Broken -and $_.Name -eq 'TreadmillRunner.Gateway.exe') } |
                    Sort-Object FullName |
                    ForEach-Object {
                        $relative = Get-ArchiveRelativePath -Root $resolvedPublish -Path $_.FullName
                        $entry = $archive.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
                        $entry.LastWriteTime = [DateTimeOffset]::new(1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
                        $source = [System.IO.File]::OpenRead($_.FullName)
                        $destination = $entry.Open()
                        try { $source.CopyTo($destination) }
                        finally { $destination.Dispose(); $source.Dispose() }
                    }
            }
            finally { $archive.Dispose() }
        }
        finally { $file.Dispose() }

        $hash = Get-Sha256Hex -Path $packagePath
        $payload = @('1', [string]$release.Version, 'stable', $packageFileName, $hash,
            '0', '100', [string]$release.Notes) -join "`n"
        $signature = $rsa.SignData(
            [System.Text.Encoding]::UTF8.GetBytes($payload),
            [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $manifest = [ordered]@{
            schemaVersion = 1
            version = [string]$release.Version
            channel = 'stable'
            packageFileName = $packageFileName
            packageSha256 = $hash
            minimumDatabaseSchemaVersion = 0
            maximumDatabaseSchemaVersion = 100
            releaseNotes = [string]$release.Notes
            signature = [Convert]::ToBase64String($signature)
        }
        $manifestPath = Join-Path $resolvedFeed "stable-$($release.Version).manifest.json"
        [System.IO.File]::WriteAllText(
            $manifestPath,
            ($manifest | ConvertTo-Json -Compress),
            [System.Text.UTF8Encoding]::new($false))
    }
}
finally {
    $certificate.Dispose()
    $rsa.Dispose()
}

Write-Host 'Acceptance feed created. No signing private key was written to disk or installed in a certificate store.'
Write-Host 'This output is test-only. Never use it as the daily stable feed.'
Write-Host "Select a fixture by copying stable-<version>.manifest.json to $(Join-Path $resolvedFeed 'stable.manifest.json')."
