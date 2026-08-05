[CmdletBinding()]
param(
    [string] $ServiceName = 'TreadmillRunnerGateway',
    [string] $ExpectedVersion,
    [Parameter(Mandatory)][string] $LanAddress,
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if ($null -eq $service) { throw 'The TreadmillRunner Windows Service is not installed.' }
if ($service.State -ne 'Running' -or $service.StartMode -ne 'Auto') {
    throw "Unexpected service lifecycle: state=$($service.State), startMode=$($service.StartMode)."
}
if ($service.PathName -notmatch '\\TreadmillRunner\\releases\\[^\\]+\\TreadmillRunner\.Gateway\.exe') {
    throw 'The service ImagePath is not an immutable versioned release.'
}

$task = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop
if ($task.Principal.UserId -ne 'SYSTEM') { throw 'The privileged updater task is not owned by SYSTEM.' }
$firewall = Get-NetFirewallRule -DisplayName 'TreadmillRunner Private LAN' -ErrorAction Stop
if ($firewall.Profile -notmatch 'Private' -or $firewall.Enabled -ne 'True') {
    throw 'The gateway firewall rule is not restricted to the Private profile.'
}
$addressFilter = $firewall | Get-NetFirewallAddressFilter
if ($addressFilter.RemoteAddress -notcontains 'LocalSubnet') {
    throw 'The gateway firewall rule is not restricted to LocalSubnet.'
}

$live = Invoke-RestMethod -Uri 'http://127.0.0.1:5180/health/live' -TimeoutSec 5
$readyResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:5180/health/ready' -UseBasicParsing -TimeoutSec 5
if ($readyResponse.StatusCode -ne 200) { throw 'Loopback readiness is not healthy.' }
$status = Invoke-RestMethod -Uri 'http://127.0.0.1:5180/api/updates/status' -TimeoutSec 5
if (-not [string]::IsNullOrWhiteSpace($ExpectedVersion) -and $status.currentVersion -ne $ExpectedVersion) {
    throw "Expected version $ExpectedVersion but the service reports $($status.currentVersion)."
}
$lanResponse = Invoke-WebRequest -Uri "http://${LanAddress}:5180/" -UseBasicParsing -TimeoutSec 5
if ($lanResponse.StatusCode -ne 200) { throw 'The private-LAN UI is unavailable.' }
$session = Invoke-WebRequest -Uri 'http://127.0.0.1:5180/api/live/session' -UseBasicParsing -SkipHttpErrorCheck -TimeoutSec 5
if ($session.StatusCode -notin 204, 200) { throw 'The live-session status endpoint is unavailable.' }

$evidence = [pscustomobject]@{
    ServiceState = $service.State
    ServiceAccount = $service.StartName
    ImagePath = $service.PathName
    UpdateTaskState = $task.State
    CurrentVersion = $status.currentVersion
    UpdateState = $status.state
    LoopbackReady = $readyResponse.StatusCode
    LanUi = "http://${LanAddress}:5180/"
    ActiveSessionHttpStatus = $session.StatusCode
}
$evidence
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedOutput) -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $resolvedOutput,
        ($evidence | ConvertTo-Json -Depth 4),
        [System.Text.UTF8Encoding]::new($false))
}
