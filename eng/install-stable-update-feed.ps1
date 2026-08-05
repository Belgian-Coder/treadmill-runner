#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][string] $SourceFeed,
    [Parameter(Mandatory)][string] $PublicCertificatePath,
    [string] $InstallRoot = "$env:ProgramFiles\TreadmillRunner",
    [string] $DataRoot = "$env:ProgramData\TreadmillRunner"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath($SourceFeed)
$resolvedCertificate = [System.IO.Path]::GetFullPath($PublicCertificatePath)
$resolvedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$destinationFeed = Join-Path $resolvedDataRoot 'updates\feed'
$pinnedCertificate = Join-Path $resolvedInstallRoot 'updater\signing.cer'
$manifestSource = Join-Path $sourceRoot 'stable.manifest.json'

if (-not (Test-Path -LiteralPath $manifestSource -PathType Leaf)) { throw 'The stable manifest is missing.' }
if (-not (Test-Path -LiteralPath $resolvedCertificate -PathType Leaf)) { throw 'The public signing certificate is missing.' }
if (-not (Test-Path -LiteralPath $pinnedCertificate -PathType Leaf)) {
    throw 'The administrator-pinned updater certificate is missing. Run install-gateway-service.ps1 first.'
}
$suppliedThumbprint = ([System.Security.Cryptography.X509Certificates.X509Certificate2]::new($resolvedCertificate)).Thumbprint
$pinnedThumbprint = ([System.Security.Cryptography.X509Certificates.X509Certificate2]::new($pinnedCertificate)).Thumbprint
if ($suppliedThumbprint -ne $pinnedThumbprint) {
    throw 'The supplied release certificate does not match the administrator-pinned updater certificate.'
}
$manifest = Get-Content -LiteralPath $manifestSource -Raw | ConvertFrom-Json
if ([string]$manifest.version -ne $Version -or [string]$manifest.channel -ne 'stable') {
    throw 'The stable manifest identity does not match the requested release.'
}
$packageName = [System.IO.Path]::GetFileName([string]$manifest.packageFileName)
if ($packageName -ne [string]$manifest.packageFileName -or -not $packageName.EndsWith('.zip', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The stable package name is invalid.'
}
$packageSource = Join-Path $sourceRoot $packageName
if (-not (Test-Path -LiteralPath $packageSource -PathType Leaf)) { throw 'The stable package is missing.' }
if ((Get-Item -LiteralPath $packageSource).Length -gt 1GB) { throw 'The stable package is too large.' }
$actualHash = (Get-FileHash -LiteralPath $packageSource -Algorithm SHA256).Hash
if ($actualHash -ne [string]$manifest.packageSha256) { throw 'The stable package hash does not match its manifest.' }

$normalizedNotes = ([string]$manifest.releaseNotes).Replace("`r`n", "`n").Replace("`r", "`n")
$payload = @(
    [string]$manifest.schemaVersion,
    [string]$manifest.version,
    [string]$manifest.channel,
    [string]$manifest.packageFileName,
    ([string]$manifest.packageSha256).ToUpperInvariant(),
    [string]$manifest.minimumDatabaseSchemaVersion,
    [string]$manifest.maximumDatabaseSchemaVersion,
    $normalizedNotes
) -join "`n"
$certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($resolvedCertificate)
$rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
try {
    if ($null -eq $rsa -or -not $rsa.VerifyData(
        [System.Text.Encoding]::UTF8.GetBytes($payload),
        [Convert]::FromBase64String([string]$manifest.signature),
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)) {
        throw 'The stable manifest signature is invalid for the supplied public certificate.'
    }
}
finally {
    if ($null -ne $rsa) { $rsa.Dispose() }
    $certificate.Dispose()
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($packageSource)
try {
    if ($archive.Entries.Count -gt 10000) { throw 'The stable package contains too many entries.' }
    $entryNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $expandedBytes = [long]0
    foreach ($entry in $archive.Entries) {
        $entryName = $entry.FullName.Replace('\', '/')
        $segments = @($entryName.Split('/') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ([string]::IsNullOrWhiteSpace($entryName) -or $entryName.StartsWith('/') -or $entryName.Contains(':') -or
            ($segments | Where-Object { $_ -eq '.' -or $_ -eq '..' }) -or -not $entryNames.Add($entryName)) {
            throw 'The stable package contains an unsafe archive path.'
        }
        if ([long]$entry.Length -gt (2GB - $expandedBytes)) { throw 'The expanded stable package is too large.' }
        $expandedBytes += [long]$entry.Length
    }
    foreach ($required in @('TreadmillRunner.Gateway.exe', 'TreadmillRunner.Migrations.exe', 'Updates\update-helper.ps1')) {
        if (-not $entryNames.Contains($required.Replace('\', '/'))) { throw "The stable package is missing $required." }
    }
}
finally { $archive.Dispose() }

if (-not $PSCmdlet.ShouldProcess($destinationFeed, "Install trusted stable update $Version")) { return }
New-Item -ItemType Directory -Path $destinationFeed -Force | Out-Null
$temporaryPackage = Join-Path $destinationFeed ".$packageName.$([Guid]::NewGuid().ToString('N')).tmp"
$temporaryManifest = Join-Path $destinationFeed ".stable.manifest.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    Copy-Item -LiteralPath $packageSource -Destination $temporaryPackage
    Copy-Item -LiteralPath $manifestSource -Destination $temporaryManifest
    Move-Item -LiteralPath $temporaryPackage -Destination (Join-Path $destinationFeed $packageName) -Force
    Move-Item -LiteralPath $temporaryManifest -Destination (Join-Path $destinationFeed 'stable.manifest.json') -Force
}
finally {
    foreach ($temporary in @($temporaryPackage, $temporaryManifest)) {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}

Write-Host "Trusted stable update $Version installed. Open Operations and select Check now."
