[CmdletBinding()]
param(
  [ValidateRange(60, 7200)][int]$DurationSeconds = 5400,
  [ValidateRange(1, 30)][int]$IntervalSeconds = 5,
  [string]$GatewayUrl = 'http://127.0.0.1:5180',
  [string]$OutputPath = (Join-Path $PSScriptRoot '..\artifacts\hardware\omega-polar-readonly-soak.csv')
)

$ErrorActionPreference = 'Stop'
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
'captured_at_utc,treadmill_state,treadmill_age_ms,treadmill_generation,speed_kph,incline_percent,heart_rate_state,heart_rate_age_ms,heart_rate_generation,heart_rate_bpm' |
  Set-Content -LiteralPath $resolvedOutput -Encoding utf8NoBOM

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($DurationSeconds)
$samples = 0
$failures = 0
while ([DateTimeOffset]::UtcNow -lt $deadline) {
  try {
    $status = Invoke-RestMethod -Uri "$GatewayUrl/api/devices/status" -TimeoutSec 5
    $captured = [DateTimeOffset]::UtcNow.ToString('O')
    $treadmillAgeMs = if ($null -eq $status.treadmillAge) { [double]::PositiveInfinity } else { ([TimeSpan]::Parse([string]$status.treadmillAge)).TotalMilliseconds }
    $heartRateAgeMs = if ($null -eq $status.heartRateAge) { [double]::PositiveInfinity } else { ([TimeSpan]::Parse([string]$status.heartRateAge)).TotalMilliseconds }
    $row = @(
      $captured,
      $status.treadmill.state,
      $treadmillAgeMs,
      $status.treadmill.connectionGeneration,
      $status.treadmillTelemetry.speedKph,
      $status.treadmillTelemetry.inclinePercent,
      $status.heartRate.state,
      $heartRateAgeMs,
      $status.heartRate.connectionGeneration,
      $status.heartRateBpm
    ) -join ','
    Add-Content -LiteralPath $resolvedOutput -Value $row -Encoding utf8NoBOM
    $samples++
    if ($status.treadmill.state -ne 'Ready' -or
        $status.heartRate.state -ne 'Ready' -or
        $treadmillAgeMs -gt 5000 -or
        $heartRateAgeMs -gt 5000) {
      $failures++
    }
  }
  catch {
    $failures++
    Add-Content -LiteralPath $resolvedOutput -Value "$( [DateTimeOffset]::UtcNow.ToString('O') ),ERROR,,,,,ERROR,,," -Encoding utf8NoBOM
  }
  Start-Sleep -Seconds $IntervalSeconds
}

Write-Host "Read-only soak complete: samples=$samples failures=$failures evidence=$resolvedOutput"
if ($samples -eq 0 -or $failures -gt 0) { exit 1 }
