[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string] $Version,
    [string] $OutputRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $projectRoot 'artifacts\releases'
}
$resolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$allowedDefaultRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts\releases'))
$allowedPrefix = $allowedDefaultRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if ($resolvedOutputRoot -ne $allowedDefaultRoot -and
    -not $resolvedOutputRoot.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Release output must remain under artifacts\releases.'
}
$releaseRoot = Join-Path $resolvedOutputRoot $Version
$publishPath = Join-Path $releaseRoot 'publish'
if (Test-Path -LiteralPath $releaseRoot) {
    throw "Release output already exists and will not be overwritten: $releaseRoot"
}
New-Item -ItemType Directory -Path $publishPath -Force | Out-Null

Push-Location $projectRoot
try {
    dotnet restore TreadmillRunner.slnx --locked-mode --force-evaluate
    if ($LASTEXITCODE -ne 0) { throw 'Locked restore failed.' }
    dotnet publish src\TreadmillRunner.Gateway\TreadmillRunner.Gateway.csproj `
        -c Release --no-restore `
        -p:Version=$Version -p:FileVersion="$Version.0" -p:InformationalVersion=$Version `
        -o $publishPath
    if ($LASTEXITCODE -ne 0) { throw 'Framework-dependent gateway publish failed.' }

    $migrationPath = Join-Path $publishPath 'TreadmillRunner.Migrations.exe'
    dotnet tool run dotnet-ef migrations bundle `
        --project src\TreadmillRunner.Infrastructure\TreadmillRunner.Infrastructure.csproj `
        --startup-project src\TreadmillRunner.Gateway\TreadmillRunner.Gateway.csproj `
        --configuration Release `
        --output $migrationPath --force --no-build
    if ($LASTEXITCODE -ne 0) { throw 'Reviewed EF Core migration bundle publish failed.' }

    foreach ($required in @(
        'TreadmillRunner.Gateway.exe',
        'TreadmillRunner.Gateway.dll',
        'TreadmillRunner.Migrations.exe',
        'Updates\update-helper.ps1',
        'wwwroot\app.css',
        'wwwroot\_framework\blazor.webassembly.js')) {
        if (-not (Test-Path -LiteralPath (Join-Path $publishPath $required) -PathType Leaf)) {
            throw "Published release is missing $required."
        }
    }

    Write-Host "Framework-dependent release $Version published to $publishPath"
}
finally {
    Pop-Location
}
