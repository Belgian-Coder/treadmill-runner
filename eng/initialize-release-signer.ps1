[CmdletBinding()]
param(
    [string] $PublicCertificatePath,
    [ValidateRange(1, 10)][int] $ValidYears = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PublicCertificatePath)) {
    $PublicCertificatePath = Join-Path $projectRoot 'artifacts\release-signing\treadmillrunner-release-signing.cer'
}
$resolvedCertificate = [System.IO.Path]::GetFullPath($PublicCertificatePath)
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts\release-signing'))
if (-not $resolvedCertificate.StartsWith($allowedRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The public release certificate must remain under artifacts\release-signing.'
}
if (Test-Path -LiteralPath $resolvedCertificate) {
    throw 'The public release certificate already exists. Reuse its matching operator-store signer or perform an explicit trust rotation.'
}

$subject = 'CN=TreadmillRunner household release signer'
$certificate = New-SelfSignedCertificate `
    -Type Custom `
    -Subject $subject `
    -CertStoreLocation 'Cert:\CurrentUser\My' `
    -KeyAlgorithm RSA `
    -KeyLength 3072 `
    -HashAlgorithm SHA256 `
    -KeyExportPolicy NonExportable `
    -KeyUsage DigitalSignature `
    -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3') `
    -NotAfter ([DateTimeOffset]::UtcNow.AddYears($ValidYears).UtcDateTime)
if (-not $certificate.HasPrivateKey) { throw 'The release signer was created without a private key.' }

$certificateDirectory = Split-Path -Parent $resolvedCertificate
New-Item -ItemType Directory -Path $certificateDirectory -Force | Out-Null
Export-Certificate -Cert $certificate -FilePath $resolvedCertificate -Type CERT | Out-Null
$metadataPath = Join-Path $certificateDirectory 'signer-metadata.json'
$metadata = [ordered]@{
    schemaVersion = 1
    subject = $certificate.Subject
    thumbprint = $certificate.Thumbprint
    notAfterUtc = $certificate.NotAfter.ToUniversalTime().ToString('O')
    publicCertificatePath = $resolvedCertificate
    privateKeyLocation = 'CurrentUser certificate store; non-exportable; interactive release operator only'
}
[System.IO.File]::WriteAllText(
    $metadataPath,
    ($metadata | ConvertTo-Json),
    [System.Text.UTF8Encoding]::new($false))

Write-Host "Release signer initialized. Thumbprint: $($certificate.Thumbprint)"
Write-Host "Public certificate: $resolvedCertificate"
Write-Host 'The private key is non-exportable and is not available to the gateway service.'

