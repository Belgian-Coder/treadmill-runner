[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release',
    [switch] $Build,
    [ValidateNotNullOrEmpty()]
    [string] $Filter = 'Category!=Browser&Category!=Soak',
    [ValidateRange(0, 10)]
    [int] $TimeoutMinutes = 0,
    [ValidateRange(30, 300)]
    [int] $StallTimeoutSeconds = 60,
    [ValidateNotNullOrEmpty()]
    [string] $ResultsDirectory = 'artifacts/test-results'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$solution = Join-Path $projectRoot 'TreadmillRunner.slnx'
$resolvedResults = [System.IO.Path]::GetFullPath($ResultsDirectory, $projectRoot)
$resolvedRoot = [System.IO.Path]::GetFullPath($projectRoot)
$effectiveTimeoutMinutes = if ($TimeoutMinutes -gt 0) {
    $TimeoutMinutes
}
elseif ([string]::Equals($Filter, 'Category!=Browser&Category!=Soak', [System.StringComparison]::OrdinalIgnoreCase)) {
    2
}
else {
    1
}
if (-not $resolvedResults.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Test results must remain inside the repository: $resolvedResults"
}
New-Item -ItemType Directory -Force -Path $resolvedResults | Out-Null
$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$standardOutput = Join-Path $resolvedResults "tests-$runStamp.stdout.log"
$standardError = Join-Path $resolvedResults "tests-$runStamp.stderr.log"

Push-Location $projectRoot
try {
    $arguments = @(
        'test', $solution,
        '--configuration', $Configuration,
        '--no-restore',
        '--filter', $Filter,
        '--logger', 'console;verbosity=normal',
        '--logger', 'trx',
        '--results-directory', $resolvedResults
    )
    if (-not $Build) { $arguments += '--no-build' }
    Write-Host "Test timeout: $effectiveTimeoutMinutes minute(s); inactivity cutoff: $StallTimeoutSeconds second(s)."
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new('dotnet')
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $arguments) { $startInfo.ArgumentList.Add($argument) }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($null -eq $process) { throw 'The dotnet test process could not be started.' }

    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $stdoutWriter = [System.IO.StreamWriter]::new($standardOutput, $false, $utf8)
    $stderrWriter = [System.IO.StreamWriter]::new($standardError, $false, $utf8)
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
    try {
        while (-not $process.HasExited -or -not $stdoutDone -or -not $stderrDone) {
            while (-not $stdoutDone -and $stdoutTask.IsCompleted) {
                $line = $stdoutTask.GetAwaiter().GetResult()
                if ($null -eq $line) { $stdoutDone = $true }
                else {
                    $stdoutWriter.WriteLine($line)
                    Write-Host $line
                    $lastOutputAt = [DateTimeOffset]::UtcNow
                    $stdoutTask = $process.StandardOutput.ReadLineAsync()
                }
            }
            while (-not $stderrDone -and $stderrTask.IsCompleted) {
                $line = $stderrTask.GetAwaiter().GetResult()
                if ($null -eq $line) { $stderrDone = $true }
                else {
                    $stderrWriter.WriteLine($line)
                    Write-Warning $line
                    $lastOutputAt = [DateTimeOffset]::UtcNow
                    $stderrTask = $process.StandardError.ReadLineAsync()
                }
            }

            $now = [DateTimeOffset]::UtcNow
            if (-not $process.HasExited -and $null -eq $terminationReason) {
                if ($now - $startedAt -ge [TimeSpan]::FromMinutes($effectiveTimeoutMinutes)) {
                    $terminationReason = "exceeded $effectiveTimeoutMinutes minute(s)"
                }
                elseif ($now - $lastOutputAt -ge [TimeSpan]::FromSeconds($StallTimeoutSeconds)) {
                    $terminationReason = "produced no progress output for $StallTimeoutSeconds seconds"
                }
                if ($null -ne $terminationReason) {
                    try { $process.Kill($true) } catch { }
                    $process.WaitForExit()
                }
            }
            if (-not $process.HasExited -and $now -ge $nextProgressAt) {
                $quietSeconds = [math]::Floor(($now - $lastOutputAt).TotalSeconds)
                Write-Host ("Tests active: {0:mm\:ss} elapsed, last output {1}s ago (PID {2})." -f ($now - $startedAt), $quietSeconds, $process.Id)
                $nextProgressAt = $now.AddSeconds(15)
            }
            if (-not $process.HasExited -or -not $stdoutDone -or -not $stderrDone) { Start-Sleep -Milliseconds 100 }
        }
    }
    finally {
        $stdoutWriter.Dispose()
        $stderrWriter.Dispose()
    }

    if ($null -ne $terminationReason) {
        throw "dotnet test $terminationReason; its exact process tree was stopped. Inspect $standardOutput and $standardError."
    }
    if ($process.ExitCode -ne 0) { throw "dotnet test failed with exit code $($process.ExitCode). Inspect $standardOutput and $standardError." }
}
finally {
    Pop-Location
}
