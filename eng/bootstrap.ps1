[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$solution = Join-Path $projectRoot 'TreadmillRunner.slnx'

Push-Location $projectRoot
try {
    $sdkVersion = (& dotnet --version).Trim()
    if (-not $sdkVersion.StartsWith('10.')) {
        throw "TreadmillRunner requires .NET SDK 10.x; found $sdkVersion."
    }

    dotnet restore $solution --use-lock-file
    if ($LASTEXITCODE -ne 0) { throw 'dotnet restore failed.' }

    Write-Host "TreadmillRunner prerequisites are ready with .NET SDK $sdkVersion."
}
finally {
    Pop-Location
}
