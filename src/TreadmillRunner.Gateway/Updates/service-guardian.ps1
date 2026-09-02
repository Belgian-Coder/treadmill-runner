[CmdletBinding()]
param(
  [string]$ServiceName = 'TreadmillRunnerGateway',
  [Parameter(Mandatory)][string]$DataRoot,
  [ValidatePattern('^http://(127\.0\.0\.1|localhost)(:\d+)?/')]
  [string]$HealthUrl = 'http://127.0.0.1:5180/health/live'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$maintenanceMarker = Join-Path $resolvedDataRoot 'updates\service-maintenance.lock'
$logDirectory = Join-Path $resolvedDataRoot 'logs'
$logPath = Join-Path $logDirectory 'service-guardian.log'
$rotatedLogPath = Join-Path $logDirectory 'service-guardian.previous.log'
$statePath = Join-Path $logDirectory 'service-guardian-state.json'
$maximumLogBytes = 1MB
$serviceDiagnosticLog = 'Microsoft-Windows-Services/Diagnostic'

function Write-GuardianLog {
  param(
    [Parameter(Mandatory)][string]$EventName,
    [System.Collections.IDictionary]$Details = @{}
  )

  New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
  if ((Test-Path -LiteralPath $logPath -PathType Leaf) -and
      (Get-Item -LiteralPath $logPath).Length -ge $maximumLogBytes) {
    Move-Item -LiteralPath $logPath -Destination $rotatedLogPath -Force
  }
  $payload = [ordered]@{
    timestampUtc = [DateTimeOffset]::UtcNow.ToString('O')
    event = $EventName
    service = $ServiceName
  }
  foreach ($key in $Details.Keys) {
    $payload[$key] = $Details[$key]
  }
  $line = $payload | ConvertTo-Json -Compress
  [System.IO.File]::AppendAllText($logPath, $line + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Write-GuardianState {
  param([Parameter(Mandatory)]$Service)

  New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
  $state = [ordered]@{
    observedUtc = [DateTimeOffset]::UtcNow.ToString('O')
    state = [string]$Service.State
    processId = [int]$Service.ProcessId
    exitCode = [int]$Service.ExitCode
  }
  [System.IO.File]::WriteAllText(
    $statePath,
    ($state | ConvertTo-Json -Compress),
    [System.Text.UTF8Encoding]::new($false))
}

function Read-GuardianState {
  if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
  try {
    return Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
  }
  catch {
    return $null
  }
}

function Get-ProcessImage {
  param([int]$ProcessId)

  if ($ProcessId -le 0) { return $null }
  try {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
    if ($null -eq $process) { return $null }
    if (-not [string]::IsNullOrWhiteSpace([string]$process.ExecutablePath)) {
      return [string]$process.ExecutablePath
    }
    return [string]$process.Name
  }
  catch {
    return $null
  }
}

function Get-RecentServiceControlEvidence {
  try {
    $events = Get-WinEvent -FilterHashtable @{
      LogName = $serviceDiagnosticLog
      Id = 200
      StartTime = (Get-Date).AddMinutes(-10)
    } -Oldest -MaxEvents 100 -ErrorAction Stop
    $controlEvent = $events | Where-Object {
      $_.Properties.Count -ge 6 -and
      [string]$_.Properties[0].Value -eq $ServiceName
    } | Select-Object -Last 1
    if ($null -eq $controlEvent) {
      return [ordered]@{ controlDiagnosticStatus = 'no-recent-control-event' }
    }

    $clientProcessId = [int]$controlEvent.Properties[4].Value
    $parentProcessId = [int]$controlEvent.Properties[5].Value
    return [ordered]@{
      controlDiagnosticStatus = 'captured'
      controlEventUtc = $controlEvent.TimeCreated.ToUniversalTime().ToString('O')
      controlCode = [uint32]$controlEvent.Properties[2].Value
      clientProcessStartKey = [string]$controlEvent.Properties[3].Value
      clientProcessId = $clientProcessId
      clientImage = Get-ProcessImage -ProcessId $clientProcessId
      parentProcessId = $parentProcessId
      parentImage = Get-ProcessImage -ProcessId $parentProcessId
    }
  }
  catch {
    $diagnosticError = $_.Exception.Message.Replace([Environment]::NewLine, ' ')
    if ($diagnosticError.Length -gt 256) { $diagnosticError = $diagnosticError.Substring(0, 256) }
    return [ordered]@{
      controlDiagnosticStatus = 'unavailable'
      controlDiagnosticErrorType = $_.Exception.GetType().FullName
      controlDiagnosticError = $diagnosticError
    }
  }
}

if (Test-Path -LiteralPath $maintenanceMarker -PathType Leaf) {
  return
}

$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
if ($null -eq $service) {
  Write-GuardianLog -EventName 'recovery-skipped' -Details @{ reason = 'not-installed' }
  return
}
if ($service.State -in @('Running', 'Start Pending')) {
  Write-GuardianState -Service $service
  return
}
if ($service.StartMode -eq 'Disabled') {
  Write-GuardianLog -EventName 'recovery-skipped' -Details @{ state = $service.State; reason = 'disabled' }
  return
}
if ($service.State -ne 'Stopped') {
  Write-GuardianLog -EventName 'recovery-skipped' -Details @{ state = $service.State; reason = 'not-stopped' }
  return
}

# An updater may create its marker after this guardian invocation begins but
# before it stops the service. Recheck after observing the stopped state so the
# in-flight update wins that race instead of having its old release restarted.
if (Test-Path -LiteralPath $maintenanceMarker -PathType Leaf) {
  return
}

try {
  $previousState = Read-GuardianState
  $diagnosticEvidence = Get-RecentServiceControlEvidence
  $recoveryDetails = [ordered]@{
    exitCode = [int]$service.ExitCode
    lastObservedRunningUtc = if ($null -eq $previousState) { $null } else { [string]$previousState.observedUtc }
    previousProcessId = if ($null -eq $previousState) { 0 } else { [int]$previousState.processId }
  }
  foreach ($key in $diagnosticEvidence.Keys) {
    $recoveryDetails[$key] = $diagnosticEvidence[$key]
  }
  if (Test-Path -LiteralPath $maintenanceMarker -PathType Leaf) {
    return
  }
  Write-GuardianLog -EventName 'recovery-start' -Details $recoveryDetails
  Start-Service -Name $ServiceName -ErrorAction Stop
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
  do {
    Start-Sleep -Milliseconds 500
    $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
  } while ($service.State -ne 'Running' -and [DateTimeOffset]::UtcNow -lt $deadline)
  if ($service.State -ne 'Running') {
    throw "Service remained $($service.State) after the recovery window."
  }

  $healthStatus = 'unavailable'
  try {
    $health = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
    $healthStatus = [string]$health.StatusCode
  }
  catch {
    $healthStatus = 'not-ready'
  }
  Write-GuardianState -Service $service
  Write-GuardianLog -EventName 'recovery-complete' -Details @{
    processId = [int]$service.ProcessId
    health = $healthStatus
  }
}
catch {
  $reason = $_.Exception.Message.Replace([Environment]::NewLine, ' ')
  if ($reason.Length -gt 512) { $reason = $reason.Substring(0, 512) }
  Write-GuardianLog -EventName 'recovery-failed' -Details @{
    reason = $reason
    errorType = $_.Exception.GetType().FullName
  }
  throw
}
