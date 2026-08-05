[CmdletBinding()]
param(
    [string] $Url = 'http://0.0.0.0:5180'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$gatewayProject = Join-Path $projectRoot 'src\TreadmillRunner.Gateway\TreadmillRunner.Gateway.csproj'
$databasePath = Join-Path $projectRoot 'data\treadmillrunner.db'
$previousDatabasePath = [Environment]::GetEnvironmentVariable('Persistence__DatabasePath', 'Process')

Push-Location $projectRoot
try {
    $env:ASPNETCORE_URLS = $Url
    $env:TreadmillRunner__Mode = 'Simulator'
    $env:Persistence__DatabasePath = $databasePath
    dotnet run --project $gatewayProject --no-launch-profile
    if ($LASTEXITCODE -ne 0) { throw 'TreadmillRunner simulator exited with an error.' }
}
finally {
    [Environment]::SetEnvironmentVariable('Persistence__DatabasePath', $previousDatabasePath, 'Process')
    Pop-Location
}
