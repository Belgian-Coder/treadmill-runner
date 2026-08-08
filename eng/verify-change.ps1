[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [string] $TestFilter,
    [string] $BrowserFilter,
    [switch] $Full,
    [switch] $IncludeConnectIq
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$testScript = Join-Path $PSScriptRoot 'test.ps1'
$browserScript = Join-Path $PSScriptRoot 'playwright.ps1'
$validateScript = Join-Path $PSScriptRoot 'validate.ps1'

if ($Full -and (-not [string]::IsNullOrWhiteSpace($TestFilter) -or -not [string]::IsNullOrWhiteSpace($BrowserFilter))) {
    throw '-Full cannot be combined with focused filters.'
}
if ($IncludeConnectIq -and -not $Full) {
    throw '-IncludeConnectIq is available only with -Full.'
}
if (-not $Full -and [string]::IsNullOrWhiteSpace($TestFilter) -and [string]::IsNullOrWhiteSpace($BrowserFilter)) {
    throw 'Focused verification requires -TestFilter, -BrowserFilter, or both. Use -Full only once at final acceptance.'
}

Push-Location $projectRoot
try {
    if ($Full) {
        Write-Host 'Running final deterministic acceptance.'
        & $validateScript -Configuration $Configuration -IncludeConnectIq:$IncludeConnectIq
        Write-Host 'Running final clean browser acceptance.'
        & $browserScript -Configuration $Configuration
        Write-Host 'Complete final acceptance passed.'
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($TestFilter)) {
        Write-Host "Running focused .NET tests: $TestFilter"
        & $testScript -Configuration $Configuration -Build -Filter $TestFilter
    }

    if (-not [string]::IsNullOrWhiteSpace($BrowserFilter)) {
        $testAssembly = Join-Path $projectRoot "tests\TreadmillRunner.E2ETests\bin\$Configuration\net10.0\TreadmillRunner.E2ETests.dll"
        $gatewayExecutable = Join-Path $projectRoot 'artifacts\e2e-host\TreadmillRunner.Gateway.exe'
        $browserBuildIsCurrent = (Test-Path -LiteralPath $testAssembly -PathType Leaf) -and
            (Test-Path -LiteralPath $gatewayExecutable -PathType Leaf)

        if ($browserBuildIsCurrent) {
            $buildTime = @(
                (Get-Item -LiteralPath $testAssembly).LastWriteTimeUtc
                (Get-Item -LiteralPath $gatewayExecutable).LastWriteTimeUtc
            ) | Sort-Object | Select-Object -First 1
            $inputRoots = @(
                (Join-Path $projectRoot 'src'),
                (Join-Path $projectRoot 'tests\TreadmillRunner.E2ETests')
            )
            $newerInput = Get-ChildItem -LiteralPath $inputRoots -Recurse -File |
                Where-Object {
                    $_.FullName -notmatch '[\\/](?:bin|obj)[\\/]' -and
                    $_.Extension -in @('.cs', '.csproj', '.razor', '.css', '.js', '.json', '.html', '.props', '.targets') -and
                    $_.LastWriteTimeUtc -gt $buildTime
                } |
                Select-Object -First 1
            $browserBuildIsCurrent = $null -eq $newerInput
        }

        if ($browserBuildIsCurrent) {
            Write-Host "Running focused browser tests with reusable build: $BrowserFilter"
            & $browserScript -Configuration $Configuration -ReuseBuild -Filter $BrowserFilter
        }
        else {
            Write-Host "Refreshing the browser build before focused tests: $BrowserFilter"
            & $browserScript -Configuration $Configuration -Filter $BrowserFilter
        }
    }

    Write-Host 'Focused verification passed. The final full gate remains pending.'
}
finally {
    Pop-Location
}
