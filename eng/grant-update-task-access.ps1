#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [string] $ServiceName = 'TreadmillRunnerGateway',
    [string] $TaskName = 'TreadmillRunnerUpdate',
    [switch] $ClearRejectedPendingPlan
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($null -eq (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    throw "The service $ServiceName is not installed."
}
$serviceSid = ([System.Security.Principal.NTAccount]::new("NT SERVICE\$ServiceName")).Translate(
    [System.Security.Principal.SecurityIdentifier]).Value
$taskService = New-Object -ComObject 'Schedule.Service'
$taskService.Connect()
$registeredTask = $taskService.GetFolder('\').GetTask($TaskName)
$registeredTask.SetSecurityDescriptor(
    "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;$serviceSid)",
    0)
if ($ClearRejectedPendingPlan) {
    $scheduledTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($scheduledTask.State -eq 'Running') {
        throw 'Refusing to clear the pending plan while the updater task is running.'
    }
    $pendingPlan = Join-Path $env:ProgramData 'TreadmillRunner\updates\plans\pending-activation.json'
    if (Test-Path -LiteralPath $pendingPlan -PathType Leaf) {
        Remove-Item -LiteralPath $pendingPlan -Force
        Write-Host 'Removed the rejected activation plan. Its database backup was preserved.'
    }
}
Write-Host "Granted $ServiceName read/execute access to $TaskName."
