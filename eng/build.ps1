[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [switch] $Restore
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$solution = Join-Path $projectRoot 'TreadmillRunner.slnx'

Push-Location $projectRoot
try {
    if ($Restore) {
        dotnet restore $solution --locked-mode
        if ($LASTEXITCODE -ne 0) { throw 'dotnet restore failed.' }
    }

    dotnet build $solution --configuration $Configuration --no-restore
    if ($LASTEXITCODE -ne 0) { throw 'dotnet build failed.' }
}
finally {
    Pop-Location
}
