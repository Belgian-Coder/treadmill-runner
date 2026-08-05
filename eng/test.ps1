[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [switch] $Build
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$solution = Join-Path $projectRoot 'TreadmillRunner.slnx'

Push-Location $projectRoot
try {
    $arguments = @('test', $solution, '--configuration', $Configuration, '--no-restore', '--filter', 'Category!=Browser&Category!=Soak', '--logger', 'console;verbosity=normal')
    if (-not $Build) { $arguments += '--no-build' }
    & dotnet @arguments
    if ($LASTEXITCODE -ne 0) { throw 'dotnet test failed.' }
}
finally {
    Pop-Location
}
