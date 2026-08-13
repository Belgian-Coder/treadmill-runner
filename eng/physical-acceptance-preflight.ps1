[CmdletBinding()]
param(
    [string] $GatewayUrl = 'http://127.0.0.1:5180',
    [string] $ExpectedTreadmillModel = 'OMEGA Z',
    [string] $ExpectedTreadmillFirmware = 'V10.23.17',
    [string] $ExpectedHeartRateDisplayName = 'Polar H10',
    [switch] $RequireFreshTelemetry,
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-SafeGet {
    param([Parameter(Mandatory)][string] $Path)

    # This acceptance preflight is intentionally GET-only. It must never scan,
    # connect, disconnect, acquire a lease, or issue a treadmill command.
    Invoke-RestMethod -Method Get -Uri ($GatewayUrl.TrimEnd('/') + $Path) -TimeoutSec 5
}

$readyResponse = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/health/ready') -UseBasicParsing -TimeoutSec 5
$bleResponse = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/health/ble') -UseBasicParsing -TimeoutSec 5
$sessionResponse = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/api/live/session') -UseBasicParsing -TimeoutSec 5
$enrollmentPayload = Invoke-SafeGet '/api/devices/enrollments'
$enrollments = @($enrollmentPayload)
$status = Invoke-SafeGet '/api/devices/status'

$treadmill = @($enrollments | Where-Object { [string]$_.role -eq 'Treadmill' })
$heartRate = @($enrollments | Where-Object {
    [string]$_.role -eq 'HeartRate' -and [string]$_.displayName -eq $ExpectedHeartRateDisplayName
})
$matchingTreadmill = @($treadmill | Where-Object {
    [string]$_.modelNumber -eq $ExpectedTreadmillModel -and
    [string]$_.firmwareRevision -eq $ExpectedTreadmillFirmware -and
    [string]$_.evidence -eq 'HardwareVerified'
})

$idle = $sessionResponse.StatusCode -eq 204
$treadmillFresh = [string]$status.treadmill.state -eq 'Ready' -and $null -ne $status.treadmillTelemetry
$heartRateFresh = [string]$status.heartRate.state -eq 'Ready' -and $null -ne $status.heartRateBpm
$prerequisites = $readyResponse.StatusCode -eq 200 -and $idle -and
    $matchingTreadmill.Count -eq 1 -and $heartRate.Count -ge 1
$readyForObservedTelemetry = $prerequisites -and $treadmillFresh -and $heartRateFresh

$evidence = [ordered]@{
    SchemaVersion = 1
    CapturedAtUtc = [DateTimeOffset]::UtcNow.ToString('O')
    GatewayUrl = $GatewayUrl
    CommandPolicy = 'GET-only; no scan, connect, disconnect, lease, session, or treadmill command request is permitted.'
    ReadyStatus = [int]$readyResponse.StatusCode
    BleHealth = [string]$bleResponse.Content
    Idle = $idle
    ExpectedTreadmill = "$ExpectedTreadmillModel / $ExpectedTreadmillFirmware"
    MatchingHardwareVerifiedTreadmills = $matchingTreadmill.Count
    MatchingHeartRateEnrollments = $heartRate.Count
    TreadmillFresh = $treadmillFresh
    HeartRateFresh = $heartRateFresh
    DeterministicPrerequisitesPassed = $prerequisites
    ReadyForOwnerObservedTelemetry = $readyForObservedTelemetry
    RemainingOwnerGates = @(
        'Owner must be present for any physical observation or command validation.',
        'A separate explicit command authorization is required before any treadmill write.',
        'Session 0 before-login, power-cycle/reconnect, simultaneous sensor, Pause, planned transitions, and representative HR workout evidence remain distinct checks.'
    )
}

$evidenceObject = [pscustomobject]$evidence
$evidenceObject
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $artifactRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts')).TrimEnd('\') + '\'
    if (-not $resolvedOutput.StartsWith($artifactRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Physical acceptance preflight evidence must remain under the project artifacts directory.'
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedOutput) -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $resolvedOutput,
        ($evidenceObject | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false))
}

if (-not $prerequisites) { throw 'Deterministic physical-acceptance prerequisites did not pass.' }
if ($RequireFreshTelemetry -and -not $readyForObservedTelemetry) {
    throw 'Fresh simultaneous treadmill and heart-rate telemetry is not available. No connection or command action was attempted.'
}
