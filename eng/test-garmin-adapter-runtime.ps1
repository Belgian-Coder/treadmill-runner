[CmdletBinding()]
param([Parameter(Mandatory)][string] $PublishPath)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$resolvedPublish = [System.IO.Path]::GetFullPath($PublishPath)
$garminRoot = Join-Path $resolvedPublish 'tools\garmin'
$runtimeRoot = Join-Path $garminRoot 'runtime'
$python = Join-Path $runtimeRoot 'python.exe'
$adapter = Join-Path $garminRoot 'garmin_activity_adapter.py'
$requirements = Join-Path $garminRoot 'requirements.lock.txt'
$notices = Join-Path $garminRoot 'THIRD-PARTY-NOTICES.md'
foreach ($required in @($python, $adapter, $requirements, $notices, (Join-Path $runtimeRoot 'LICENSE.txt'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Garmin adapter release content is missing: $required" }
}
foreach ($package in @('garminconnect', 'curl_cffi', 'requests', 'ua_generator')) {
    if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot "Lib\site-packages\$package"))) {
        throw "Garmin adapter package is missing: $package"
    }
}

$request = '{"operation":"probe"}'
$result = $request | & $python -I -B $adapter 2>&1
if ($LASTEXITCODE -ne 0) { throw "Garmin adapter readiness probe exited with code $LASTEXITCODE." }
$line = ($result | Out-String).Trim()
try { $payload = $line | ConvertFrom-Json -ErrorAction Stop }
catch { throw 'Garmin adapter readiness probe returned invalid JSON.' }
if ($payload.state -ne 'ready') { throw 'Garmin adapter readiness probe did not report ready.' }
if ($line -match '(?i)([A-Z]:\\|password|tokenStore|pythonpath)') {
    throw 'Garmin adapter readiness output exposed a path or sensitive field.'
}
Write-Host 'Bundled Garmin adapter readiness probe passed.'
