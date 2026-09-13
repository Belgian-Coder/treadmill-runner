[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [string] $TestFilter,
    [string] $BrowserFilter,
    [switch] $Full,
    [switch] $Release,
    [switch] $NoBrowser,
    [switch] $IncludeConnectIq
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$testScript = Join-Path $PSScriptRoot 'test.ps1'
$browserScript = Join-Path $PSScriptRoot 'playwright.ps1'
$validateScript = Join-Path $PSScriptRoot 'validate.ps1'

if ($Full -and $Release) {
    throw '-Full and -Release are mutually exclusive.'
}
$acceptanceRun = $Full -or $Release
if ($acceptanceRun -and (-not [string]::IsNullOrWhiteSpace($TestFilter) -or -not [string]::IsNullOrWhiteSpace($BrowserFilter))) {
    throw '-Full and -Release cannot be combined with focused filters.'
}
if ($IncludeConnectIq -and -not $acceptanceRun) {
    throw '-IncludeConnectIq is available only with -Full or -Release.'
}
if ($NoBrowser -and -not $acceptanceRun) {
    throw '-NoBrowser is available only with -Full or -Release.'
}
if (-not $acceptanceRun -and [string]::IsNullOrWhiteSpace($TestFilter) -and [string]::IsNullOrWhiteSpace($BrowserFilter)) {
    throw 'Focused verification requires -TestFilter, -BrowserFilter, or both. Use -Release for routine release acceptance or -Full for the exhaustive gate.'
}

Push-Location $projectRoot
try {
    if ($acceptanceRun) {
        $acceptanceStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $deterministicStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $acceptanceLevel = if ($Full) { 'exhaustive' } else { 'release' }
        $releaseTestFilter = '(FullyQualifiedName~TreadmillRunner.Core.Tests|FullyQualifiedName~TreadmillRunner.Protocols.Tests|Category=ReleaseSmoke)&Category!=Browser&Category!=Soak'
        $effectiveTestFilter = if ($Release) { $releaseTestFilter } else { 'Category!=Browser&Category!=Soak' }
        Write-Host "Running $acceptanceLevel deterministic acceptance."
        # Deterministic tests never need the optimized native WebAssembly output.
        # Browser acceptance or release packaging owns that build when required.
        & $validateScript -Configuration $Configuration -IncludeConnectIq:$IncludeConnectIq -SkipNativeWeb -TestFilter $effectiveTestFilter
        $deterministicStopwatch.Stop()
        $browserSeconds = 0
        $browserScope = 'none'
        if ($NoBrowser) {
            Write-Host 'Skipping browser acceptance because the release diff contains no browser-affecting files.'
        }
        else {
            $browserStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            if ($Release) {
                $browserScope = 'release'
                Write-Host 'Running the risk-selected release browser smoke suite.'
                & $browserScript -Configuration $Configuration -Filter 'Category=Browser&Category=ReleaseSmoke' -SkipNativeWeb
            }
            else {
                $browserScope = 'exhaustive'
                Write-Host 'Running exhaustive browser acceptance.'
                & $browserScript -Configuration $Configuration
            }
            $browserStopwatch.Stop()
            $browserSeconds = [math]::Round($browserStopwatch.Elapsed.TotalSeconds, 3)
        }
        $acceptanceStopwatch.Stop()
        $head = (& git rev-parse HEAD).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($head)) {
            throw 'Final acceptance could not resolve the validated commit.'
        }
        if ([string]::IsNullOrWhiteSpace((& git status --porcelain))) {
            $receiptPath = Join-Path $projectRoot 'artifacts\validation\full-acceptance.json'
            New-Item -ItemType Directory -Path (Split-Path -Parent $receiptPath) -Force | Out-Null
            [System.IO.File]::WriteAllText(
                $receiptPath,
                ([ordered]@{
                    schemaVersion = 3
                    sourceRevision = $head
                    configuration = $Configuration
                    includeConnectIq = [bool]$IncludeConnectIq
                    browserAccepted = -not [bool]$NoBrowser
                    acceptanceLevel = $acceptanceLevel
                    browserScope = $browserScope
                    deterministicSeconds = [math]::Round($deterministicStopwatch.Elapsed.TotalSeconds, 3)
                    browserSeconds = $browserSeconds
                    totalSeconds = [math]::Round($acceptanceStopwatch.Elapsed.TotalSeconds, 3)
                    completedAtUtc = [DateTimeOffset]::UtcNow.ToString('O')
                } | ConvertTo-Json),
                [System.Text.UTF8Encoding]::new($false))
            Write-Host "Recorded reusable full-acceptance receipt for $head."
        }
        else {
            Write-Host 'Full acceptance passed with uncommitted changes; no reusable release receipt was recorded.'
        }
        Write-Host ("{0} acceptance passed in {1:n1}s (deterministic {2:n1}s, browser {3:n1}s)." -f `
            $acceptanceLevel, $acceptanceStopwatch.Elapsed.TotalSeconds, $deterministicStopwatch.Elapsed.TotalSeconds, $browserSeconds)
        return
    }

    if (-not [string]::IsNullOrWhiteSpace($TestFilter)) {
        Write-Host "Running focused .NET tests: $TestFilter"
        & $testScript -Configuration $Configuration -Build -Filter $TestFilter
    }

    if (-not [string]::IsNullOrWhiteSpace($BrowserFilter)) {
        $testAssembly = Join-Path $projectRoot "tests\TreadmillRunner.E2ETests\bin\$Configuration\net10.0\TreadmillRunner.E2ETests.dll"
        $gatewayExecutable = Join-Path $projectRoot 'artifacts\e2e-host\TreadmillRunner.Gateway.exe'
        $gatewayPublishStamp = Join-Path $projectRoot 'artifacts\e2e-host\.publish-complete'
        $browserBuildIsCurrent = (Test-Path -LiteralPath $testAssembly -PathType Leaf) -and
            (Test-Path -LiteralPath $gatewayExecutable -PathType Leaf) -and
            (Test-Path -LiteralPath $gatewayPublishStamp -PathType Leaf)

        if ($browserBuildIsCurrent) {
            $buildTime = @(
                (Get-Item -LiteralPath $testAssembly).LastWriteTimeUtc
                (Get-Item -LiteralPath $gatewayPublishStamp).LastWriteTimeUtc
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
