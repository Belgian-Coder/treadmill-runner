#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string] $ServiceName = 'TreadmillRunnerGateway',
    [ValidateRange(1, 168)][int] $SinceHours = 24,
    [ValidateRange(1, 1000)][int] $MaximumEvents = 100,
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$dataRoot = 'C:\ProgramData\TreadmillRunner'
$logPath = Join-Path $dataRoot 'logs\service-guardian.log'
$statePath = Join-Path $dataRoot 'logs\service-guardian-state.json'
$diagnosticLogName = 'Microsoft-Windows-Services/Diagnostic'
$startTime = (Get-Date).AddHours(-$SinceHours)
$diagnosticLog = Get-WinEvent -ListLog $diagnosticLogName -ErrorAction Stop

$controlEvents = @()
if ($diagnosticLog.IsEnabled) {
    $controlEvents = Get-WinEvent -FilterHashtable @{
        LogName = $diagnosticLogName
        Id = 200
        StartTime = $startTime
    } -Oldest -MaxEvents $MaximumEvents -ErrorAction SilentlyContinue | Where-Object {
        $_.Properties.Count -ge 6 -and
        [string]$_.Properties[0].Value -eq $ServiceName
    } | ForEach-Object {
        [pscustomobject]@{
            TimeUtc = $_.TimeCreated.ToUniversalTime().ToString('O')
            ServiceName = [string]$_.Properties[0].Value
            DisplayName = [string]$_.Properties[1].Value
            ControlCode = [uint32]$_.Properties[2].Value
            ClientProcessStartKey = [string]$_.Properties[3].Value
            ClientProcessId = [int]$_.Properties[4].Value
            ParentProcessId = [int]$_.Properties[5].Value
        }
    }
}

$guardianEvents = if (Test-Path -LiteralPath $logPath -PathType Leaf) {
    @(Get-Content -LiteralPath $logPath -Tail 1000 | ForEach-Object {
        try { $_ | ConvertFrom-Json } catch { [pscustomobject]@{ legacyLine = $_ } }
    })
} else { @() }
$lastObservation = if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
} else { $null }
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
$result = [pscustomobject]@{
    CollectedUtc = [DateTimeOffset]::UtcNow.ToString('O')
    Service = if ($null -eq $service) { $null } else { [pscustomobject]@{
        Name = $service.Name
        State = $service.State
        StartMode = $service.StartMode
        ProcessId = [int]$service.ProcessId
        ExitCode = [int]$service.ExitCode
    } }
    DiagnosticChannel = [pscustomobject]@{
        Name = $diagnosticLog.LogName
        Enabled = $diagnosticLog.IsEnabled
        MaximumSizeInBytes = $diagnosticLog.MaximumSizeInBytes
        LogMode = [string]$diagnosticLog.LogMode
    }
    LastHealthyObservation = $lastObservation
    GuardianEvents = $guardianEvents
    ServiceControlEvents = @($controlEvents)
}

$result
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $artifactRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts'))
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    $artifactPrefix = $artifactRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolvedOutput.StartsWith($artifactPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Service recovery evidence must remain under the project artifacts directory.'
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedOutput) -Force | Out-Null
    [System.IO.File]::WriteAllText(
        $resolvedOutput,
        ($result | ConvertTo-Json -Depth 8),
        [System.Text.UTF8Encoding]::new($false))
}
