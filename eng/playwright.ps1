[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [switch] $InstallBrowsers,
    [ValidateNotNullOrEmpty()]
    [string] $Filter = 'Category=Browser'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$checker = Join-Path $projectRoot '.agents\skills\playwright-integration\scripts\check_playwright_readiness.py'
$evidenceDir = Join-Path $projectRoot 'validation\playwright'
$report = Join-Path $evidenceDir 'readiness.json'
$project = Join-Path $projectRoot 'tests\TreadmillRunner.E2ETests\TreadmillRunner.E2ETests.csproj'
$gatewayProject = Join-Path $projectRoot 'src\TreadmillRunner.Gateway\TreadmillRunner.Gateway.csproj'
$publishedHost = Join-Path $projectRoot 'artifacts\e2e-host'
$wasmCleaner = Join-Path $PSScriptRoot 'clean-wasm-publish.ps1'

New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
& python -B $checker --project-root $projectRoot --output-json $report
if ($LASTEXITCODE -ne 0) { throw "Playwright readiness did not pass. Inspect $report." }

Push-Location $projectRoot
try {
    & dotnet restore $project --locked-mode
    if ($LASTEXITCODE -ne 0) { throw 'Playwright project restore failed.' }

    & dotnet build $project --configuration $Configuration --no-restore
    if ($LASTEXITCODE -ne 0) { throw 'Playwright project build failed.' }

    & $wasmCleaner -Configuration $Configuration
    if ($LASTEXITCODE -ne 0) { throw 'Generated WebAssembly publish-state cleanup failed.' }

    $resolvedArtifacts = [System.IO.Path]::GetFullPath($publishedHost)
    $resolvedRoot = [System.IO.Path]::GetFullPath($projectRoot)
    if (-not $resolvedArtifacts.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace E2E host outside the repository: $resolvedArtifacts"
    }
    if (Test-Path -LiteralPath $resolvedArtifacts) {
        Remove-Item -LiteralPath $resolvedArtifacts -Recurse -Force
    }
    & dotnet publish $gatewayProject --configuration $Configuration --no-restore --output $resolvedArtifacts
    if ($LASTEXITCODE -ne 0) { throw 'Published E2E gateway build failed.' }

    if ($InstallBrowsers) {
        $installer = Join-Path $projectRoot "tests\TreadmillRunner.E2ETests\bin\$Configuration\net10.0\playwright.ps1"
        & $installer install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium installation failed.' }
    }

    & dotnet test $project --configuration $Configuration --no-build --no-restore --filter $Filter --logger 'console;verbosity=normal'
    if ($LASTEXITCODE -ne 0) { throw 'Playwright browser tests failed.' }
}
finally {
    Pop-Location
}
