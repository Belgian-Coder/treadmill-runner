#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [switch] $ExitWithDiagnosticMask,
    [switch] $ExitWithJournalCode,
    [ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version = '1.5.6',
    [string] $ExportLatestJournalPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop
$taskInfo = Get-ScheduledTaskInfo -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop
$checks = [ordered]@{
    TaskHasProtectedRoots = $task.Actions.Arguments -like '*-InstallRoot*' -and $task.Actions.Arguments -like '*-DataRoot*'
    HelperExists = Test-Path 'C:\Program Files\TreadmillRunner\updater\update-helper.ps1' -PathType Leaf
    CertificateExists = Test-Path 'C:\Program Files\TreadmillRunner\updater\signing.cer' -PathType Leaf
    PendingPlanExists = Test-Path 'C:\ProgramData\TreadmillRunner\updates\plans\pending-activation.json' -PathType Leaf
    StageExists = Test-Path (Join-Path 'C:\ProgramData\TreadmillRunner\updates\staging' $Version) -PathType Container
    BackupExists = $null -ne (Get-ChildItem 'C:\ProgramData\TreadmillRunner\backups\pre-update-*.db' -ErrorAction SilentlyContinue | Select-Object -First 1)
}
if (-not [string]::IsNullOrWhiteSpace($ExportLatestJournalPath)) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $artifactRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts'))
    $destination = [System.IO.Path]::GetFullPath($ExportLatestJournalPath)
    $artifactPrefix = $artifactRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $destination.StartsWith($artifactPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'The exported journal must remain under the project artifacts directory.'
    }
    $journal = Get-ChildItem 'C:\ProgramData\TreadmillRunner\updates\plans\transaction-*.json' -ErrorAction Stop |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $journal) { throw 'No update transaction journal exists.' }
    Copy-Item -LiteralPath $journal.FullName -Destination $destination -Force
    return
}
if ($ExitWithDiagnosticMask) {
    $mask = 0
    $bit = 1
    foreach ($value in $checks.Values) {
        if ($value) { $mask += $bit }
        $bit *= 2
    }
    exit $mask
}
if ($ExitWithJournalCode) {
    $journal = Get-ChildItem 'C:\ProgramData\TreadmillRunner\updates\plans\transaction-*.json' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $journal) { exit 1 }
    $payload = Get-Content -LiteralPath $journal.FullName -Raw | ConvertFrom-Json
    $reason = [string]$payload.reason
    if ([string]$payload.version -ne $Version) { exit 2 }
    if ($reason -match 'immutable target release') { exit 3 }
    if ($reason -match 'signed package|staged manifest|certificate|database backup') { exit 4 }
    if ($reason -match 'signature|hash') { exit 5 }
    if ($reason -match 'newer than the installed') { exit 6 }
    if ($reason -match 'assembly manifest') { exit 11 }
    if ($reason -match 'Could not load file or assembly') { exit 12 }
    if ($reason -match 'not a valid assembly') { exit 13 }
    if ($reason -match 'Assembly|version') { exit 7 }
    if ($reason -match 'migration') { exit 8 }
    if ($reason -match 'service|binary path') { exit 9 }
    if ($reason -match 'ready|health') { exit 10 }
    exit 99
}

[pscustomobject]@{
    Checks = $checks
    LastTaskResult = $taskInfo.LastTaskResult
    LastRunTime = $taskInfo.LastRunTime
    TaskState = $task.State
    Action = $task.Actions.Arguments
}
