[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [switch] $InstallBrowsers,
    [ValidateNotNullOrEmpty()]
    [string] $Filter = 'Category=Browser',
    [switch] $ReuseBuild,
    [ValidateRange(0, 15)]
    [int] $TimeoutMinutes = 0,
    [ValidateRange(30, 300)]
    [int] $StallTimeoutSeconds = 90,
    [ValidateNotNullOrEmpty()]
    [string] $ResultsDirectory = 'artifacts/test-results'
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
$publishStamp = Join-Path $publishedHost '.publish-complete'
$wasmCleaner = Join-Path $PSScriptRoot 'clean-wasm-publish.ps1'
$databaseScript = Join-Path $PSScriptRoot 'database.ps1'
$databaseTemplate = Join-Path $projectRoot 'artifacts\e2e-template\e2e-template.db'
$resolvedResults = [System.IO.Path]::GetFullPath($ResultsDirectory, $projectRoot)
$resolvedRoot = [System.IO.Path]::GetFullPath($projectRoot)
$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runBaseName = "browser-$runStamp"
$effectiveTimeoutMinutes = if ($TimeoutMinutes -gt 0) {
    $TimeoutMinutes
}

elseif ([string]::Equals($Filter, 'Category=Browser', [System.StringComparison]::OrdinalIgnoreCase)) {
    5
}
else {
    2
}

function Remove-GeneratedDirectory {
    param([Parameter(Mandatory)][string] $Path)

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        if (-not (Test-Path -LiteralPath $Path)) { return }
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return
        }
        catch {
            if (-not (Test-Path -LiteralPath $Path)) { return }
            if ($attempt -eq 3) { throw }
            Start-Sleep -Milliseconds 200
        }
    }
}

if (-not $resolvedResults.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Browser results must remain inside the repository: $resolvedResults"
}

function Invoke-BrowserTests {
    param(
        [string[]] $Arguments,
        [string] $TemplateDatabasePath,
        [string] $RunLabel,
        [int] $RunTimeoutMinutes
    )

    $runStandardOutput = Join-Path $resolvedResults "$runBaseName-$RunLabel.stdout.log"
    $runStandardError = Join-Path $resolvedResults "$runBaseName-$RunLabel.stderr.log"

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new('dotnet')
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.Environment['TreadmillRunner__E2ETemplateDatabasePath'] = $TemplateDatabasePath
    foreach ($argument in $Arguments) { $startInfo.ArgumentList.Add($argument) }

    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw 'The Playwright test process could not be started.' }
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $stdoutWriter = [System.IO.StreamWriter]::new($runStandardOutput, $false, $utf8)
    $stderrWriter = [System.IO.StreamWriter]::new($runStandardError, $false, $utf8)
    $stdoutWriter.AutoFlush = $true
    $stderrWriter.AutoFlush = $true
    $stdoutTask = $process.StandardOutput.ReadLineAsync()
    $stderrTask = $process.StandardError.ReadLineAsync()
    $stdoutDone = $false
    $stderrDone = $false
    $startedAt = [DateTimeOffset]::UtcNow
    $lastOutputAt = $startedAt
    $nextProgressAt = $startedAt.AddSeconds(15)
    $terminationReason = $null
    $failureDetectedAt = $null
    try {
        while (-not $process.HasExited -or -not $stdoutDone -or -not $stderrDone) {
            while (-not $stdoutDone -and $stdoutTask.IsCompleted) {
                $line = $stdoutTask.GetAwaiter().GetResult()
                if ($null -eq $line) {
                    $stdoutDone = $true
                }
                else {
                    $stdoutWriter.WriteLine($line)
                    Write-Host $line
                    $lastOutputAt = [DateTimeOffset]::UtcNow
                    if ($null -eq $failureDetectedAt -and $line -match '^\s*Failed\s+TreadmillRunner\.E2ETests\.') {
                        $failureDetectedAt = $lastOutputAt
                        Write-Warning 'Browser test failure detected; stopping remaining work after a two-second log flush.'
                    }
                    $stdoutTask = $process.StandardOutput.ReadLineAsync()
                }
            }
            while (-not $stderrDone -and $stderrTask.IsCompleted) {
                $line = $stderrTask.GetAwaiter().GetResult()
                if ($null -eq $line) {
                    $stderrDone = $true
                }
                else {
                    $stderrWriter.WriteLine($line)
                    Write-Warning $line
                    $lastOutputAt = [DateTimeOffset]::UtcNow
                    $stderrTask = $process.StandardError.ReadLineAsync()
                }
            }

            $now = [DateTimeOffset]::UtcNow
            if (-not $process.HasExited -and $null -eq $terminationReason) {
                if ($now - $startedAt -ge [TimeSpan]::FromMinutes($RunTimeoutMinutes)) {
                    $terminationReason = "exceeded $RunTimeoutMinutes minute(s)"
                }
                elseif ($now - $lastOutputAt -ge [TimeSpan]::FromSeconds($StallTimeoutSeconds)) {
                    $terminationReason = "produced no progress output for $StallTimeoutSeconds seconds"
                }
                elseif ($null -ne $failureDetectedAt -and $now - $failureDetectedAt -ge [TimeSpan]::FromSeconds(2)) {
                    $terminationReason = 'reported a test failure (fail-fast)'
                }
                if ($null -ne $terminationReason) {
                    try { $process.Kill($true) } catch { }
                    $process.WaitForExit()
                }
            }
            if (-not $process.HasExited -and $now -ge $nextProgressAt) {
                $quietSeconds = [math]::Floor(($now - $lastOutputAt).TotalSeconds)
                Write-Host ("Playwright active: {0:mm\:ss} elapsed, last output {1}s ago (PID {2})." -f ($now - $startedAt), $quietSeconds, $process.Id)
                $nextProgressAt = $now.AddSeconds(15)
            }
            if (-not $process.HasExited -or -not $stdoutDone -or -not $stderrDone) {
                Start-Sleep -Milliseconds 100
            }
        }
    }
    finally {
        $stdoutWriter.Dispose()
        $stderrWriter.Dispose()
    }
    if ($null -ne $terminationReason) {
        throw "Playwright $terminationReason; its exact process tree was stopped. Inspect $runStandardOutput and $runStandardError."
    }
    return $process.ExitCode
}

New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
New-Item -ItemType Directory -Force -Path $resolvedResults | Out-Null
Write-Host "Playwright timeout: $effectiveTimeoutMinutes minute(s)."
Write-Host "Playwright inactivity cutoff: $StallTimeoutSeconds second(s)."
$reuseReadiness = $false
if (Test-Path -LiteralPath $report -PathType Leaf) {
    try {
        $readiness = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
        $reuseReadiness = $readiness.PSObject.Properties.Name -contains 'ok' -and [bool]$readiness.ok
        if ($reuseReadiness) {
            $reportTime = (Get-Item -LiteralPath $report).LastWriteTimeUtc
            $readinessInputs = @(
                Get-ChildItem -LiteralPath (Join-Path $projectRoot '.agents\skills\playwright-integration\scripts') -Filter '*.py' -File
                Get-Item -LiteralPath $project
                Get-Item -LiteralPath (Join-Path $projectRoot 'Directory.Packages.props')
                Get-Item -LiteralPath (Join-Path $projectRoot 'global.json')
            )
            $reuseReadiness = -not ($readinessInputs | Where-Object { $_.LastWriteTimeUtc -gt $reportTime } | Select-Object -First 1)
        }
    }
    catch {
        $reuseReadiness = $false
    }
}
if ($reuseReadiness) {
    Write-Host "Reusing passed Playwright readiness evidence: $report"
}
else {
    & python -B $checker --project-root $projectRoot --output-json $report
    if ($LASTEXITCODE -ne 0) { throw "Playwright readiness did not pass. Inspect $report." }
}

Push-Location $projectRoot
try {
    $resolvedArtifacts = [System.IO.Path]::GetFullPath($publishedHost)
    if (-not $resolvedArtifacts.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace E2E host outside the repository: $resolvedArtifacts"
    }

    if (-not $ReuseBuild) {
        & dotnet restore $project --locked-mode
        if ($LASTEXITCODE -ne 0) { throw 'Playwright project restore failed.' }

        & dotnet build $project --configuration $Configuration --no-restore --disable-build-servers
        if ($LASTEXITCODE -ne 0) { throw 'Playwright project build failed.' }

        & $wasmCleaner -Configuration $Configuration
        if ($LASTEXITCODE -ne 0) { throw 'Generated WebAssembly publish-state cleanup failed.' }

        if (Test-Path -LiteralPath $resolvedArtifacts) {
            Remove-GeneratedDirectory -Path $resolvedArtifacts
        }
        & dotnet publish $gatewayProject --configuration $Configuration --no-restore --disable-build-servers --output $resolvedArtifacts -m:1
        if ($LASTEXITCODE -ne 0) { throw 'Published E2E gateway build failed.' }
        [System.IO.File]::WriteAllText(
            $publishStamp,
            [DateTimeOffset]::UtcNow.ToString('O'),
            [System.Text.UTF8Encoding]::new($false))
    }
    else {
        $testAssembly = Join-Path $projectRoot "tests\TreadmillRunner.E2ETests\bin\$Configuration\net10.0\TreadmillRunner.E2ETests.dll"
        $gatewayExecutable = Join-Path $publishedHost 'TreadmillRunner.Gateway.exe'
        if (-not (Test-Path -LiteralPath $testAssembly) -or
            -not (Test-Path -LiteralPath $gatewayExecutable) -or
            -not (Test-Path -LiteralPath $publishStamp)) {
            throw '-ReuseBuild requires an existing E2E test build and published gateway. Run once without -ReuseBuild.'
        }
    }

    $newestMigration = Get-ChildItem (Join-Path $projectRoot 'src\TreadmillRunner.Infrastructure\Persistence\Migrations') -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    $templateIsStale = -not (Test-Path -LiteralPath $databaseTemplate) -or
        ($null -ne $newestMigration -and $newestMigration.LastWriteTimeUtc -gt (Get-Item -LiteralPath $databaseTemplate).LastWriteTimeUtc)
    if ($templateIsStale) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $databaseTemplate) -Force | Out-Null
        foreach ($path in @($databaseTemplate, "$databaseTemplate-shm", "$databaseTemplate-wal")) {
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
        }
        & $databaseScript -Action Update -DatabasePath $databaseTemplate
        if ($LASTEXITCODE -ne 0) { throw 'The E2E template database could not be migrated.' }
    }
    else {
        Write-Host "Reusing migrated E2E database template: $databaseTemplate"
    }

    if ($InstallBrowsers) {
        $installer = Join-Path $projectRoot "tests\TreadmillRunner.E2ETests\bin\$Configuration\net10.0\playwright.ps1"
        & $installer install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium installation failed.' }
    }

    $testRuns = if ([string]::Equals($Filter, 'Category=Browser', [System.StringComparison]::OrdinalIgnoreCase)) {
        @(
            [pscustomobject]@{ Label = 'functional'; Filter = 'Category=Browser&Category!=Performance'; TimeoutMinutes = $effectiveTimeoutMinutes },
            [pscustomobject]@{ Label = 'performance'; Filter = 'Category=Browser&Category=Performance'; TimeoutMinutes = 2 }
        )
    }
    else {
        @([pscustomobject]@{ Label = 'focused'; Filter = $Filter; TimeoutMinutes = $effectiveTimeoutMinutes })
    }

    foreach ($testRun in $testRuns) {
        $trxName = "$runBaseName-$($testRun.Label).trx"
        Write-Host "Running $($testRun.Label) browser tests: $($testRun.Filter)"
        $testArguments = @(
            'test', $project,
            '--configuration', $Configuration,
            '--no-build', '--no-restore',
            '--filter', $testRun.Filter,
            '--logger', 'console;verbosity=normal',
            '--logger', "trx;LogFileName=$trxName",
            '--results-directory', $resolvedResults
        )
        $testExitCode = Invoke-BrowserTests -Arguments $testArguments -TemplateDatabasePath $databaseTemplate -RunLabel $testRun.Label -RunTimeoutMinutes $testRun.TimeoutMinutes
        if ($testExitCode -ne 0) { throw "Playwright $($testRun.Label) tests failed with exit code $testExitCode. Inspect $(Join-Path $resolvedResults $trxName)." }
        Write-Host "Playwright $($testRun.Label) tests passed. TRX: $(Join-Path $resolvedResults $trxName)"
    }
}
finally {
    Pop-Location
}
