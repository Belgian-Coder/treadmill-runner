[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][string] $PublishPath,
    [Parameter(Mandatory)][string] $PublicCertificatePath,
    [Parameter(Mandatory)][string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedPublish = [System.IO.Path]::GetFullPath($PublishPath)
$resolvedCertificate = [System.IO.Path]::GetFullPath($PublicCertificatePath)
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$allowedOutputRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts\releases'))
$allowedPrefix = $allowedOutputRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $resolvedOutput.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Installer bundle output must remain under artifacts\releases.'
}
if (-not (Test-Path -LiteralPath $resolvedPublish -PathType Container)) { throw 'PublishPath is missing.' }
if (-not (Test-Path -LiteralPath $resolvedCertificate -PathType Leaf)) { throw 'PublicCertificatePath is missing.' }
if (Test-Path -LiteralPath $resolvedOutput) { throw 'The installer bundle already exists and will not be overwritten.' }
foreach ($required in @('TreadmillRunner.Gateway.exe', 'TreadmillRunner.Migrations.exe', 'Updates\update-helper.ps1', 'Updates\service-guardian.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedPublish $required) -PathType Leaf)) {
        throw "PublishPath is missing $required."
    }
}
& (Join-Path $PSScriptRoot 'test-garmin-adapter-runtime.ps1') -PublishPath $resolvedPublish
if ($LASTEXITCODE -ne 0) { throw 'Bundled Garmin adapter validation failed.' }

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) "TreadmillRunner-installer-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    Copy-Item -LiteralPath $resolvedPublish -Destination (Join-Path $temporaryRoot 'app') -Recurse
    Copy-Item -LiteralPath $resolvedCertificate -Destination (Join-Path $temporaryRoot 'treadmillrunner-release-signing.cer')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install-gateway-service.ps1') -Destination $temporaryRoot
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Install-TreadmillRunner.ps1') -Destination $temporaryRoot
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Install-TreadmillRunner.cmd') -Destination $temporaryRoot
    $instructions = @"
INSTALL TREADMILLRUNNER

1. Extract this complete ZIP to a normal folder.
2. Make sure this Windows 11 x64 computer uses a Private network profile.
3. Install Microsoft ASP.NET Core Runtime 10 x64 if it is not already installed:
   https://dotnet.microsoft.com/download/dotnet/10.0
4. Double-click Install-TreadmillRunner.cmd and approve the administrator prompt.

The installer opens http://localhost:5180 when the service is ready.
Phones and tablets on the same trusted household network use:
http://<NUC-hostname>:5180

Keep the treadmill safety key fitted and physical Stop reachable. Do not expose
port 5180 to the Internet or a guest network.

Full guide: https://github.com/belgian-coder/treadmill-runner/blob/main/docs/installation.md
"@
    [System.IO.File]::WriteAllText(
        (Join-Path $temporaryRoot 'INSTALL.txt'),
        $instructions,
        [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText(
        (Join-Path $temporaryRoot 'release.json'),
        (([ordered]@{ schemaVersion = 1; version = $Version } | ConvertTo-Json -Compress)),
        [System.Text.UTF8Encoding]::new($false))
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $temporaryRoot,
        $resolvedOutput,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $false)
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) { Remove-Item -LiteralPath $temporaryRoot -Recurse -Force }
}
Write-Host "End-user installer bundle written to $resolvedOutput"
