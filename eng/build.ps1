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

    # The solution contains both the Blazor Web project and the Gateway that references it.
    # Serial project builds prevent both nodes from mutating static-web-assets output at once.
    dotnet build $solution --configuration $Configuration --no-restore --disable-build-servers -m:1
    if ($LASTEXITCODE -ne 0) { throw 'dotnet build failed.' }
}
finally {
    Pop-Location
}
