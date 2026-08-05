[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $PublishPath,
    [ValidateNotNullOrEmpty()][string] $BuildPython = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$pythonVersion = '3.12.10'
$archiveName = "python-$pythonVersion-embed-amd64.zip"
$archiveUri = "https://www.python.org/ftp/python/$pythonVersion/$archiveName"
$expectedArchiveSha256 = '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3'
$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedPublish = [System.IO.Path]::GetFullPath($PublishPath)
if (-not (Test-Path -LiteralPath $resolvedPublish -PathType Container)) { throw 'PublishPath is missing.' }

$garminRoot = Join-Path $resolvedPublish 'tools\garmin'
$runtimeRoot = Join-Path $garminRoot 'runtime'
$requirements = Join-Path $garminRoot 'requirements.lock.txt'
$adapter = Join-Path $garminRoot 'garmin_activity_adapter.py'
$notices = Join-Path $garminRoot 'THIRD-PARTY-NOTICES.md'
foreach ($required in @($requirements, $adapter, $notices)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Portable Garmin runtime input is missing: $required" }
}
if (Test-Path -LiteralPath $runtimeRoot) { throw "Garmin runtime output already exists: $runtimeRoot" }

$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) "TreadmillRunner-garmin-runtime-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    $archivePath = Join-Path $temporaryRoot $archiveName
    Invoke-WebRequest -Uri $archiveUri -OutFile $archivePath
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToUpperInvariant()
    if ($actualHash -ne $expectedArchiveSha256) {
        throw "Python runtime hash mismatch. Expected $expectedArchiveSha256, received $actualHash."
    }

    New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $runtimeRoot
    $pthPath = Join-Path $runtimeRoot 'python312._pth'
    if (-not (Test-Path -LiteralPath $pthPath -PathType Leaf)) { throw 'The pinned Python runtime is missing python312._pth.' }
    $pth = @('python312.zip', '.', 'Lib\site-packages', 'import site') -join "`r`n"
    [System.IO.File]::WriteAllText($pthPath, "$pth`r`n", [System.Text.UTF8Encoding]::new($false))

    $sitePackages = Join-Path $runtimeRoot 'Lib\site-packages'
    New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
    & $BuildPython -m pip install `
        --disable-pip-version-check --no-input --ignore-installed --no-compile `
        --require-hashes --only-binary=:all: `
        --target $sitePackages -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Hash-locked Garmin adapter dependency installation failed.' }

    $licensePath = Join-Path $runtimeRoot 'LICENSE.txt'
    if (-not (Test-Path -LiteralPath $licensePath -PathType Leaf)) { throw 'The bundled Python license is missing.' }
    & (Join-Path $PSScriptRoot 'test-garmin-adapter-runtime.ps1') -PublishPath $resolvedPublish
    if ($LASTEXITCODE -ne 0) { throw 'The bundled Garmin adapter readiness probe failed.' }
}
catch {
    if (Test-Path -LiteralPath $runtimeRoot) { Remove-Item -LiteralPath $runtimeRoot -Recurse -Force }
    throw
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) { Remove-Item -LiteralPath $temporaryRoot -Recurse -Force }
}

Write-Host "Pinned offline Garmin adapter runtime $pythonVersion staged at $runtimeRoot"
