[CmdletBinding()]
param(
    [string] $Python = 'python',
    [string] $TargetDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($TargetDirectory)) {
    $TargetDirectory = Join-Path $projectRoot 'artifacts/garmin-python'
}
$resolvedTarget = [System.IO.Path]::GetFullPath($TargetDirectory, $projectRoot)
New-Item -ItemType Directory -Path $resolvedTarget -Force | Out-Null
& $Python -m pip install --disable-pip-version-check --no-input --target $resolvedTarget `
    --require-hashes --only-binary=:all: `
    --requirement (Join-Path $projectRoot 'tools/garmin/requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'The pinned Garmin adapter dependency installation failed.' }
& $Python -c "import sys; sys.path.insert(0, r'$resolvedTarget'); import garminconnect; print('Garmin adapter dependency ready.')"
if ($LASTEXITCODE -ne 0) { throw 'The installed Garmin adapter could not be imported.' }
