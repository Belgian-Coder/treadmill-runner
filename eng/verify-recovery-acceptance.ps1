[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')][string] $Configuration = 'Release'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$testProject = Join-Path $projectRoot 'tests\TreadmillRunner.IntegrationTests\TreadmillRunner.IntegrationTests.csproj'
$filter = @(
    'FullyQualifiedName~UpdateManagerTests',
    'FullyQualifiedName~SqliteRestoreServiceTests',
    'FullyQualifiedName~PersistenceBackupRoundTripTests',
    'FullyQualifiedName~ReleaseScriptContractTests'
) -join '|'

Push-Location $projectRoot
try {
    & dotnet test $testProject `
        --configuration $Configuration `
        --no-restore `
        --disable-build-servers `
        --filter $filter `
        --logger 'console;verbosity=minimal'
    if ($LASTEXITCODE -ne 0) { throw 'Isolated recovery acceptance tests failed.' }
}
finally {
    Pop-Location
}

Write-Host 'Isolated update rollback, SQLite restore, backup round-trip, and acceptance-script contracts passed.'
Write-Host 'No installed service, production database, update feed, scheduled task, release, or treadmill command was changed.'
