[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [switch] $Build
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $projectRoot 'tests\TreadmillRunner.IntegrationTests\TreadmillRunner.IntegrationTests.csproj'

Push-Location $projectRoot
try {
    & dotnet restore $project --locked-mode
    if ($LASTEXITCODE -ne 0) { throw 'Locked restore failed.' }

    $arguments = @(
        'test',
        $project,
        '--configuration', $Configuration,
        '--no-restore',
        '--filter', 'Category=Soak',
        '--logger', 'console;verbosity=normal'
    )
    if (-not $Build) { $arguments += '--no-build' }
    & dotnet @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Four-hour persistence soak failed.' }
}
finally {
    Pop-Location
}
