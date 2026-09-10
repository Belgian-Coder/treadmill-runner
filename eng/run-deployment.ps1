#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $ExpectedVersion,
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-fA-F]{40}$')][string] $ExpectedCommit,
    [Parameter(Mandatory)][ValidatePattern('^v\d+\.\d+\.\d+$')][string] $ExpectedRelease,
    [ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string] $Repository = 'belgian-coder/treadmill-runner',
    [ValidatePattern('^http://(127\.0\.0\.1|localhost)(:\d+)?$')][string] $GatewayUrl = 'http://127.0.0.1:5180',
    [string] $InstallRoot = "$env:ProgramFiles\TreadmillRunner",
    [string] $DataRoot = "$env:ProgramData\TreadmillRunner",
    [string] $ExpectedTreadmillModel = 'OMEGA Z',
    [string] $ExpectedTreadmillFirmware = 'V10.23.17',
    [string] $ExpectedHeartRateDisplayName = 'Polar heart-rate sensor',
    [switch] $Activate,
    [ValidateSet('ACTIVATE')][string] $Confirmation,
    [switch] $DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-GhJson {
    param([Parameter(Mandatory)][string[]] $Arguments)
    $errorPath = [System.IO.Path]::GetTempFileName()
    try {
        $lines = @(& gh @Arguments 2> $errorPath)
        if ($LASTEXITCODE -ne 0) {
            $details = if (Test-Path -LiteralPath $errorPath) { (Get-Content -LiteralPath $errorPath -Raw).Trim() } else { '' }
            throw "GitHub CLI failed: $details"
        }
        return ($lines -join "`n") | ConvertFrom-Json
    }
    finally {
        Remove-Item -LiteralPath $errorPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-Sha256Hex {
    param([Parameter(Mandatory)][string] $Path)
    return ([string](Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash).ToLowerInvariant()
}

function Get-ZipEntrySha256 {
    param([Parameter(Mandatory)] $Archive, [Parameter(Mandatory)][string] $Name)
    $entry = $Archive.GetEntry($Name)
    if ($null -eq $entry) { throw "Signed package is missing required entry $Name." }
    $input = $entry.Open()
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($sha.ComputeHash($input))).Replace('-', '').ToLowerInvariant() }
    finally { $input.Dispose(); $sha.Dispose() }
}

function Invoke-GetJson {
    param([Parameter(Mandatory)][string] $Path)
    return Invoke-RestMethod -Method Get -Uri ($GatewayUrl.TrimEnd('/') + $Path) -TimeoutSec 10
}

function Expand-JsonArray {
    param([Parameter(Mandatory)] $Value)
    foreach ($item in @($Value)) {
        if ($item -is [System.Array]) {
            foreach ($nested in $item) { $nested }
        }
        elseif ($null -ne $item) {
            $item
        }
    }
}

function Convert-TerminalSessionState {
    param([Parameter(Mandatory)] $Value)
    $text = [string]$Value
    if ($text -eq '4' -or $text -eq 'Completed') { return 'Completed' }
    if ($text -eq '5' -or $text -eq 'Stopped') { return 'Stopped' }
    if ($text -eq '6' -or $text -eq 'Interrupted') { return 'Interrupted' }
    if ($text -eq '7' -or $text -eq 'Faulted') { return 'Faulted' }
    return $null
}

function Convert-ToCanonicalJson {
    param([Parameter(Mandatory)] $Value)
    return ($Value | ConvertTo-Json -Depth 100 -Compress)
}

function Assert-Idle {
    $response = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/api/live/session') -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -eq 204) { return }
    if ($response.StatusCode -ne 200) {
        throw "The gateway is not idle immediately before deployment (live/session returned HTTP $($response.StatusCode))."
    }

    # A completed session remains in the in-memory coordinator until the next
    # service lifetime. Treating that response as idle would weaken the 204
    # activation gate, while sending an End command would be invalid for a
    # terminal session. Verify the exact terminal session is durably present
    # before using this already-elevated coordinator to restart the service.
    $snapshot = $response.Content | ConvertFrom-Json
    $sessionId = [string]$snapshot.sessionId
    $sessionState = Convert-TerminalSessionState -Value $snapshot.live.sessionState
    if ([string]::IsNullOrWhiteSpace($sessionId) -or $sessionId -notmatch '^[0-9a-fA-F-]{36}$' -or
        $null -eq $sessionState) {
        throw "The gateway has a nonterminal or unrecognized live session; refusing to restart it ($([string]$snapshot.live.sessionState)/$sessionId)."
    }
    $historyResponse = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + "/api/history/$sessionId") -UseBasicParsing -TimeoutSec 10
    if ($historyResponse.StatusCode -ne 200) {
        throw "The terminal live session $sessionId is not durably available in history (HTTP $($historyResponse.StatusCode)); refusing to restart the gateway."
    }
    $history = $historyResponse.Content | ConvertFrom-Json
    $historyState = Convert-TerminalSessionState -Value $history.state
    if ($historyState -ne $sessionState) {
        throw "The live session $sessionId and durable history disagree ($sessionState/$([string]$history.state)); refusing to restart the gateway."
    }

    Restart-Service -Name 'TreadmillRunnerGateway' -Force -ErrorAction Stop
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
    do {
        try {
            $ready = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/health/ready') -UseBasicParsing -TimeoutSec 3
            if ($ready.StatusCode -eq 200) {
                $afterRestart = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/api/live/session') -UseBasicParsing -TimeoutSec 3
                if ($afterRestart.StatusCode -eq 204) { return }
            }
        }
        catch { }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'The gateway did not return to HTTP 204 idle state after the verified terminal-session restart.'
}

function Get-ServiceExecutablePath {
    param([Parameter(Mandatory)][string] $ImagePath)
    $candidate = $ImagePath.Trim()
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw 'The Windows Service ImagePath is empty.'
    }
    $executable = if ($candidate -match '^"([^"]+)"$') {
        $Matches[1]
    }
    elseif ($candidate -match '^[^"]+\.exe$') {
        $candidate
    }
    else {
        throw 'The Windows Service ImagePath is not a single executable path.'
    }
    $canonical = [System.IO.Path]::GetFullPath($executable)
    if (-not $executable.Equals($canonical, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'The Windows Service ImagePath is not a canonical executable path.'
    }
    return $canonical
}

function Get-CurrentInstalledRelease {
    $service = Get-CimInstance Win32_Service -Filter "Name='TreadmillRunnerGateway'" -ErrorAction Stop
    if ($null -eq $service) { throw 'The gateway service is not installed.' }
    $executable = [System.IO.Path]::GetFullPath((Get-ServiceExecutablePath -ImagePath ([string]$service.PathName)))
    $releaseRoot = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot 'releases'))
    $releasePrefix = $releaseRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $executable.StartsWith($releasePrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        [System.IO.Path]::GetFileName($executable) -cne 'TreadmillRunner.Gateway.exe' -or
        -not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "The installed service executable is outside the immutable release contract: $executable"
    }
    $version = Split-Path -Leaf (Split-Path -Parent $executable)
    if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "The installed release directory has an invalid version: $version" }
    return [pscustomobject]@{ Version = $version; Executable = $executable }
}

function Expand-VerifiedRepairSource {
    param([Parameter(Mandatory)][string]$PackagePath, [Parameter(Mandatory)][string]$DestinationRoot)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $entries = [ordered]@{
        'TreadmillRunner.Gateway.exe' = 'TreadmillRunner.Gateway.exe'
        'TreadmillRunner.Migrations.exe' = 'TreadmillRunner.Migrations.exe'
        'Updates/update-helper.ps1' = 'Updates\update-helper.ps1'
        'Updates/service-guardian.ps1' = 'Updates\service-guardian.ps1'
    }
    $maximumBytes = 1GB
    $expandedBytes = [long]0
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    $archive = [System.IO.Compression.ZipFile]::OpenRead($PackagePath)
    try {
        foreach ($entryName in $entries.Keys) {
            $entry = $archive.GetEntry($entryName)
            if ($null -eq $entry) { throw "The verified package is missing repair entry $entryName." }
            if ($entry.FullName.Replace('\', '/') -cne $entryName) { throw "The repair entry name is not exact: $entryName" }
            if ([long]$entry.Length -gt ($maximumBytes - $expandedBytes)) { throw 'The bounded repair source exceeds its expanded size limit.' }
            $expandedBytes += [long]$entry.Length
            $destination = [System.IO.Path]::GetFullPath((Join-Path $DestinationRoot $entries[$entryName]))
            $destinationPrefix = [System.IO.Path]::GetFullPath($DestinationRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
            if (-not $destination.StartsWith($destinationPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'The repair entry escapes its bounded source directory.' }
            $parent = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
            $input = $entry.Open()
            try {
                $output = [System.IO.File]::Open($destination, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
                try {
                    $input.CopyTo($output, 64 * 1024)
                    $output.Flush($true)
                }
                finally { $output.Dispose() }
            }
            finally { $input.Dispose() }
            if ((Get-Item -LiteralPath $destination).Length -ne [long]$entry.Length) { throw "The bounded repair entry size changed: $entryName" }
        }
    }
    finally { $archive.Dispose() }
    return [System.IO.Path]::GetFullPath($DestinationRoot)
}

function Repair-ProtectedInfrastructure {
    param(
        [Parameter(Mandatory)][string]$PackagePath,
        [Parameter(Mandatory)][string]$CertificatePath,
        [Parameter(Mandatory)][string]$ExpectedHelperHash,
        [Parameter(Mandatory)][string]$ExpectedGuardianHash,
        [Parameter(Mandatory)][string]$CurrentVersion,
        [Parameter(Mandatory)][string]$RepairRoot
    )
    Expand-VerifiedRepairSource -PackagePath $PackagePath -DestinationRoot $RepairRoot | Out-Null
    & (Join-Path $PSScriptRoot 'install-gateway-service.ps1') `
        -Version $CurrentVersion `
        -ReleasePath $RepairRoot `
        -PublicCertificatePath $CertificatePath `
        -InstallRoot $InstallRoot `
        -DataRoot $DataRoot `
        -RepairUpdateInfrastructureOnly
    if ($LASTEXITCODE -ne 0) { throw 'The protected update infrastructure bootstrap failed.' }
    $protectedHelper = Join-Path $InstallRoot 'updater\update-helper.ps1'
    $protectedGuardian = Join-Path $InstallRoot 'updater\service-guardian.ps1'
    if ((Get-Sha256Hex -Path $protectedHelper) -cne $ExpectedHelperHash -or
        (Get-Sha256Hex -Path $protectedGuardian) -cne $ExpectedGuardianHash) {
        throw 'The protected updater scripts did not match the verified package after bootstrap.'
    }
    $service = Get-CimInstance Win32_Service -Filter "Name='TreadmillRunnerGateway'" -ErrorAction Stop
    if ($null -eq $service -or [string]$service.State -ne 'Running' -or [string]$service.StartMode -ne 'Auto') {
        throw 'The gateway service was not ready after protected update infrastructure bootstrap.'
    }
    $ready = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/health/ready') -UseBasicParsing -TimeoutSec 10
    if ($ready.StatusCode -ne 200) { throw 'The gateway readiness endpoint did not return HTTP 200 after bootstrap.' }
}

function Invoke-PendingActivationReconciliation {
    param(
        [Parameter(Mandatory)][string]$ResolvedInstallRoot,
        [Parameter(Mandatory)][string]$ResolvedDataRoot
    )
    $pendingPlanPath = Join-Path $ResolvedDataRoot 'updates\plans\pending-activation.json'
    $maintenanceMarkerPath = Join-Path $ResolvedDataRoot 'updates\service-maintenance.lock'
    $maintenanceMutex = [System.Threading.Mutex]::new($false, 'Global\TreadmillRunnerGateway.Maintenance')
    $maintenanceMutexHeld = $false
    try {
        try { $maintenanceMutexAcquired = $maintenanceMutex.WaitOne(30000) }
        catch [System.Threading.AbandonedMutexException] { $maintenanceMutexAcquired = $true }
        if (-not $maintenanceMutexAcquired) { throw 'The update maintenance lock could not be acquired for pending-plan reconciliation.' }
        $maintenanceMutexHeld = $true

        # Establish the no-plan case under the same gate as plan publication.
        if (-not (Test-Path -LiteralPath $pendingPlanPath)) { return }
        if (-not (Test-Path -LiteralPath $pendingPlanPath -PathType Leaf) -or
            ((Get-Item -LiteralPath $pendingPlanPath -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'The pending activation inbox is not a regular protected file.'
        }
        if (Test-Path -LiteralPath $maintenanceMarkerPath) {
            throw 'The pending activation cannot be reconciled while update maintenance is active.'
        }
        $plan = Get-Content -LiteralPath $pendingPlanPath -Raw -ErrorAction Stop | ConvertFrom-Json
        $transactionId = [string]$plan.TransactionId
        $version = [string]$plan.Version
        if ($transactionId -notmatch '^[0-9a-f]{32}$' -or $version -notmatch '^\d+\.\d+\.\d+$') {
            throw 'The pending activation plan transaction identity is invalid.'
        }
        $journalPath = Join-Path (Split-Path -Parent $pendingPlanPath) ("transaction-{0}.json" -f $transactionId)
        if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf) -or
            ((Get-Item -LiteralPath $journalPath -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'The pending activation transaction journal is missing.'
        }
        $journal = Get-Content -LiteralPath $journalPath -Raw -ErrorAction Stop | ConvertFrom-Json
        if ([int]$journal.schemaVersion -ne 1 -or [string]$journal.transactionId -cne $transactionId -or
            [string]$journal.version -cne $version -or [string]$journal.state -cne 'RolledBack') {
            throw 'The pending activation transaction journal is not the exact terminal RolledBack transaction.'
        }
        $journalHash = Get-Sha256Hex -Path $journalPath
        $planFile = Get-Item -LiteralPath $pendingPlanPath -Force
        $planHash = Get-Sha256Hex -Path $pendingPlanPath
        $planIdentity = '{0}|{1}|{2}|{3}|{4}' -f $transactionId, $version, $planHash,
            [long]$planFile.Length, $planFile.LastWriteTimeUtc.Ticks

        $task = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        $taskInfo = Get-ScheduledTaskInfo -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        if ([string]$task.State -eq 'Running' -or [string]$task.Settings.MultipleInstances -ine 'IgnoreNew') {
            throw 'The protected update task is already processing a run or does not ignore overlapping runs.'
        }
        $previousLastRunTime = $taskInfo.LastRunTime
        $expectedHelperPath = [System.IO.Path]::GetFullPath((Join-Path $ResolvedInstallRoot 'updater\update-helper.ps1'))
        $expectedPlanPath = [System.IO.Path]::GetFullPath($pendingPlanPath)
        $expectedInstallRoot = [System.IO.Path]::GetFullPath($ResolvedInstallRoot)
        $expectedDataRoot = [System.IO.Path]::GetFullPath($ResolvedDataRoot)
        $expectedArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -PlanPath "{1}" -InstallRoot "{2}" -DataRoot "{3}" -HealthUrl "{4}"' -f
            $expectedHelperPath, $expectedPlanPath, $expectedInstallRoot, $expectedDataRoot, 'http://127.0.0.1:5180/health/ready'
        $actions = @($task.Actions)
        if ($actions.Count -ne 1 -or [string]$actions[0].Execute -ine 'powershell.exe' -or
            [string]$actions[0].Arguments -cne $expectedArguments -or
            [string]$task.Principal.UserId -ine 'SYSTEM' -or
            [string]$task.Principal.LogonType -ine 'ServiceAccount' -or
            [string]$task.Principal.RunLevel -ine 'Highest') {
            throw 'The protected update task does not match the fixed SYSTEM/highest-privilege contract.'
        }

        # Revalidate the transaction identity while owning the same mutex that
        # creates/replaces pending plans. Keep it held through schtasks and
        # until the scheduler reports a fresh Running invocation. The task is
        # IgnoreNew, and UpdateManager must acquire this mutex and refuses to
        # create a replacement while this pending plan still exists, so the
        # helper can only consume the captured transaction after the gate opens.
        if (Test-Path -LiteralPath $maintenanceMarkerPath) {
            throw 'The pending activation maintenance marker appeared before dispatch.'
        }
        if (-not (Test-Path -LiteralPath $pendingPlanPath -PathType Leaf) -or
            ((Get-Item -LiteralPath $pendingPlanPath -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'The pending activation plan changed before dispatch.'
        }
        $dispatchPlan = Get-Content -LiteralPath $pendingPlanPath -Raw -ErrorAction Stop | ConvertFrom-Json
        $dispatchFile = Get-Item -LiteralPath $pendingPlanPath -Force
        $dispatchIdentity = '{0}|{1}|{2}|{3}|{4}' -f [string]$dispatchPlan.TransactionId, [string]$dispatchPlan.Version,
            (Get-Sha256Hex -Path $pendingPlanPath), [long]$dispatchFile.Length, $dispatchFile.LastWriteTimeUtc.Ticks
        if ($dispatchIdentity -cne $planIdentity) {
            throw 'The pending activation plan changed before dispatch.'
        }
        $dispatchAtUtc = [DateTime]::UtcNow
        & schtasks.exe /Run /TN '\TreadmillRunnerUpdate' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The protected update task could not be started for pending-plan reconciliation.' }
        $startDeadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
        $dispatchObserved = $false
        do {
            $task = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
            $taskInfo = Get-ScheduledTaskInfo -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
            $freshLastRun = $null -ne $taskInfo.LastRunTime -and $taskInfo.LastRunTime -ne [DateTime]::MinValue -and
                $taskInfo.LastRunTime.ToUniversalTime() -ge $dispatchAtUtc.AddSeconds(-1) -and
                ($null -eq $previousLastRunTime -or $taskInfo.LastRunTime -ne $previousLastRunTime)
            if ([string]$task.State -eq 'Running' -and $freshLastRun) { $dispatchObserved = $true; break }
            Start-Sleep -Milliseconds 250
        } while ([DateTimeOffset]::UtcNow -lt $startDeadline)
        if (-not $dispatchObserved) { throw 'The protected update task was accepted but did not show a fresh Running invocation.' }
    }
    finally {
        if ($maintenanceMutexHeld) { $maintenanceMutex.ReleaseMutex() }
        $maintenanceMutex.Dispose()
    }
    $taskStarted = $false
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
    do {
        $task = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        $taskInfo = Get-ScheduledTaskInfo -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        if ([string]$task.State -eq 'Running') { $taskStarted = $true }
        elseif ($null -ne $taskInfo.LastRunTime -and $taskInfo.LastRunTime -ne [DateTime]::MinValue -and
            $taskInfo.LastRunTime.ToUniversalTime() -ge $dispatchAtUtc.AddSeconds(-5)) { $taskStarted = $true }
        if ($taskStarted -and [string]$task.State -ne 'Running') {
            if ([uint32]$taskInfo.LastTaskResult -ne 0) {
                throw "The protected update task failed pending-plan reconciliation with result $([uint32]$taskInfo.LastTaskResult)."
            }
            if (-not (Test-Path -LiteralPath $pendingPlanPath) -and
                -not (Test-Path -LiteralPath $maintenanceMarkerPath)) {
                if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf) -or
                    ((Get-Item -LiteralPath $journalPath -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                    throw 'The pending activation transaction journal disappeared after reconciliation.'
                }
                $terminalJournal = Get-Content -LiteralPath $journalPath -Raw -ErrorAction Stop | ConvertFrom-Json
                if ((Get-Sha256Hex -Path $journalPath) -cne $journalHash -or
                    [int]$terminalJournal.schemaVersion -ne 1 -or
                    [string]$terminalJournal.transactionId -cne $transactionId -or
                    [string]$terminalJournal.version -cne $version -or
                    [string]$terminalJournal.state -cne 'RolledBack') {
                    throw 'The pending activation transaction journal changed after reconciliation.'
                }
                return
            }
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'The protected update task did not reconcile the pending activation plan within 120 seconds.'
}

function Invoke-StaleActivatingCoordinatorNormalization {
    param(
        [Parameter(Mandatory)][string]$ResolvedInstallRoot,
        [Parameter(Mandatory)][string]$ResolvedDataRoot,
        [Parameter(Mandatory)][string]$ExpectedCurrentVersion,
        [Parameter(Mandatory)][string]$ExpectedCurrentExecutable
    )
    $maintenanceMutex = [System.Threading.Mutex]::new($false, 'Global\TreadmillRunnerGateway.Maintenance')
    $maintenanceMutexHeld = $false
    try {
        try { $maintenanceMutexAcquired = $maintenanceMutex.WaitOne(30000) }
        catch [System.Threading.AbandonedMutexException] { $maintenanceMutexAcquired = $true }
        if (-not $maintenanceMutexAcquired) { throw 'The update maintenance lock could not be acquired for stale coordinator normalization.' }
        $maintenanceMutexHeld = $true

        $status = Invoke-GetJson '/api/updates/status'
        if ([string]$status.state -cne 'Activating') { return }
        $staleStatusVersion = [string]$status.availableVersion
        if ($staleStatusVersion -notmatch '^\d+\.\d+\.\d+$' -or
            [string]$status.currentVersion -cne $ExpectedCurrentVersion) {
            throw 'The Activating update status has no exact stale target or does not match the current service version.'
        }
        $liveBeforeRestart = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/api/live/session') -UseBasicParsing -TimeoutSec 10
        if ($liveBeforeRestart.StatusCode -ne 204) {
            throw 'Stale coordinator normalization requires HTTP 204 live-session idle state before restart.'
        }

        $pendingPlanPath = Join-Path $ResolvedDataRoot 'updates\plans\pending-activation.json'
        $maintenanceMarkerPath = Join-Path $ResolvedDataRoot 'updates\service-maintenance.lock'
        foreach ($path in @($pendingPlanPath, $maintenanceMarkerPath)) {
            if (Test-Path -LiteralPath $path) {
                throw 'Stale coordinator normalization requires an absent pending plan and maintenance marker.'
            }
        }

        $task = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        $taskInfo = Get-ScheduledTaskInfo -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        if ([string]$task.State -cne 'Ready' -or [string]$task.Settings.MultipleInstances -ine 'IgnoreNew' -or
            [uint32]$taskInfo.LastTaskResult -ne 0) {
            throw 'Stale coordinator normalization requires the protected update task to be Ready with result 0 and IgnoreNew.'
        }

        $databaseStatus = Invoke-GetJson '/api/operations/database/status'
        if ([string]$databaseStatus.state -notin @('Healthy', 'HealthyWithBackupWarning') -or
            [bool]$databaseStatus.recoveryRequired) {
            throw 'Stale coordinator normalization requires a healthy database operation state.'
        }
        $databaseIdentity = '{0}|{1}|{2}|{3}|{4}|{5}' -f [string]$databaseStatus.state,
            [string]$databaseStatus.updatedAtUtc, [string]$databaseStatus.lastQuickCheckAtUtc,
            [string]$databaseStatus.lastFullCheckAtUtc, [string]$databaseStatus.lastMaintenanceAtUtc,
            [string]$databaseStatus.lastBackupAtUtc

        $service = Get-CimInstance Win32_Service -Filter "Name='TreadmillRunnerGateway'" -ErrorAction Stop
        if ($null -eq $service -or [string]$service.State -ne 'Running' -or [string]$service.StartMode -ne 'Auto') {
            throw 'Stale coordinator normalization requires the gateway service to be Running with Automatic start.'
        }
        $currentRelease = Get-CurrentInstalledRelease
        $expectedExecutable = [System.IO.Path]::GetFullPath($ExpectedCurrentExecutable)
        if ([string]$currentRelease.Version -cne $ExpectedCurrentVersion -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$currentRelease.Executable, $expectedExecutable)) {
            throw 'Stale coordinator normalization found an unexpected current service release or executable path.'
        }

        $planRoot = [System.IO.Path]::GetFullPath((Join-Path $ResolvedDataRoot 'updates\plans'))
        if (-not (Test-Path -LiteralPath $planRoot -PathType Container) -or
            ((Get-Item -LiteralPath $planRoot -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'The update plan root is not a regular protected directory.'
        }
        $matchingJournals = @()
        foreach ($journalFile in @(Get-ChildItem -LiteralPath $planRoot -Filter 'transaction-*.json' -Force -ErrorAction Stop)) {
            if ($journalFile.PSIsContainer -or ($journalFile.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
                throw 'The update transaction journal set contains a non-regular entry.'
            }
            $journalTransactionId = [System.IO.Path]::GetFileNameWithoutExtension($journalFile.Name).Substring('transaction-'.Length)
            if ($journalTransactionId -notmatch '^[0-9a-f]{32}$') { throw 'The update transaction journal set contains an invalid transaction identity.' }
            $candidate = Get-Content -LiteralPath $journalFile.FullName -Raw -ErrorAction Stop | ConvertFrom-Json
            if ([int]$candidate.schemaVersion -ne 1 -or [string]$candidate.transactionId -cne $journalTransactionId -or
                [string]$candidate.version -notmatch '^\d+\.\d+\.\d+$' -or
                [string]$candidate.state -notin @('Activated', 'RolledBack', 'RollbackFailed')) {
                throw "The update transaction journal is invalid: $($journalFile.Name)"
            }
            if ([string]$candidate.version -ceq $staleStatusVersion) {
                if ([string]$candidate.state -cne 'RolledBack') {
                    throw 'The Activating status target has a non-RolledBack terminal journal.'
                }
                $matchingJournals += [pscustomobject]@{
                    Path = $journalFile.FullName
                    TransactionId = $journalTransactionId
                    Hash = Get-Sha256Hex -Path $journalFile.FullName
                    Journal = $candidate
                }
            }
        }
        if ($matchingJournals.Count -ne 1) {
            throw "The Activating status target does not have exactly one matching RolledBack journal ($($matchingJournals.Count))."
        }

        try {
            $journalOccurredAtUtc = ([DateTimeOffset]::Parse([string]$matchingJournals[0].Journal.occurredAtUtc)).ToUniversalTime()
            $statusCheckedAtUtc = ([DateTimeOffset]::Parse([string]$status.lastCheckedAtUtc)).ToUniversalTime()
        }
        catch { throw 'The stale Activating status or terminal RolledBack journal timestamp is invalid.' }
        $serviceProcess = Get-Process -Id ([int]$service.ProcessId) -ErrorAction Stop
        $serviceStartedAtUtc = ([DateTimeOffset]$serviceProcess.StartTime).ToUniversalTime()
        if ($serviceStartedAtUtc -ge $journalOccurredAtUtc -or $serviceStartedAtUtc -ge $statusCheckedAtUtc) {
            throw 'The current service process does not predate the stale status and terminal rollback journal.'
        }

        $databaseStatusRecheck = Invoke-GetJson '/api/operations/database/status'
        $databaseIdentityRecheck = '{0}|{1}|{2}|{3}|{4}|{5}' -f [string]$databaseStatusRecheck.state,
            [string]$databaseStatusRecheck.updatedAtUtc, [string]$databaseStatusRecheck.lastQuickCheckAtUtc,
            [string]$databaseStatusRecheck.lastFullCheckAtUtc, [string]$databaseStatusRecheck.lastMaintenanceAtUtc,
            [string]$databaseStatusRecheck.lastBackupAtUtc
        if ([string]$databaseStatusRecheck.state -notin @('Healthy', 'HealthyWithBackupWarning') -or
            [bool]$databaseStatusRecheck.recoveryRequired -or $databaseIdentityRecheck -cne $databaseIdentity) {
            throw 'The database operation state changed during stale coordinator proof.'
        }

        Restart-Service -Name 'TreadmillRunnerGateway' -Force -ErrorAction Stop
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
        $normalized = $false
        do {
            try {
                $ready = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/health/ready') -UseBasicParsing -TimeoutSec 3
                $idle = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/api/live/session') -UseBasicParsing -TimeoutSec 3
                if ($ready.StatusCode -eq 200 -and $idle.StatusCode -eq 204) { $normalized = $true; break }
            }
            catch { }
            Start-Sleep -Seconds 2
        } while ([DateTimeOffset]::UtcNow -lt $deadline)
        if (-not $normalized) {
            throw 'The gateway did not return to readiness and HTTP 204 idle state after stale coordinator normalization.'
        }
        $afterStatus = Invoke-GetJson '/api/updates/status'
        $afterLive = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/api/live/session') -UseBasicParsing -TimeoutSec 10
        if ([string]$afterLive.StatusCode -cne '204' -or [string]$afterStatus.state -ceq 'Activating' -or
            [string]$afterStatus.currentVersion -cne $ExpectedCurrentVersion) {
            throw 'The gateway did not reach a non-Activating HTTP 204 terminal state after stale coordinator normalization.'
        }
        $afterDatabaseStatus = Invoke-GetJson '/api/operations/database/status'
        if ([string]$afterDatabaseStatus.state -notin @('Healthy', 'HealthyWithBackupWarning') -or
            [bool]$afterDatabaseStatus.recoveryRequired) {
            throw 'The database operation state was not healthy after stale coordinator normalization.'
        }
        if ((Test-Path -LiteralPath $pendingPlanPath) -or (Test-Path -LiteralPath $maintenanceMarkerPath)) {
            throw 'A pending activation plan or maintenance marker reappeared during stale coordinator normalization.'
        }
        $afterTask = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        $afterTaskInfo = Get-ScheduledTaskInfo -TaskName 'TreadmillRunnerUpdate' -TaskPath '\' -ErrorAction Stop
        if ([string]$afterTask.State -cne 'Ready' -or [string]$afterTask.Settings.MultipleInstances -ine 'IgnoreNew' -or
            [uint32]$afterTaskInfo.LastTaskResult -ne 0) {
            throw 'The protected update task was not Ready with result 0 after stale coordinator normalization.'
        }
        $afterService = Get-CimInstance Win32_Service -Filter "Name='TreadmillRunnerGateway'" -ErrorAction Stop
        $afterRelease = Get-CurrentInstalledRelease
        if ($null -eq $afterService -or [string]$afterService.State -ne 'Running' -or [string]$afterService.StartMode -ne 'Auto' -or
            [string]$afterRelease.Version -cne $ExpectedCurrentVersion -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$afterRelease.Executable, $expectedExecutable)) {
            throw 'The gateway service release was not consistent after stale coordinator normalization.'
        }
        if (-not (Test-Path -LiteralPath $matchingJournals[0].Path -PathType Leaf) -or
            ((Get-Item -LiteralPath $matchingJournals[0].Path -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -or
            (Get-Sha256Hex -Path $matchingJournals[0].Path) -cne $matchingJournals[0].Hash) {
            throw 'The exact terminal RolledBack journal changed during stale coordinator normalization.'
        }
        $afterJournal = Get-Content -LiteralPath $matchingJournals[0].Path -Raw -ErrorAction Stop | ConvertFrom-Json
        if ([string]$afterJournal.transactionId -cne $matchingJournals[0].TransactionId -or
            [string]$afterJournal.version -cne $staleStatusVersion -or [string]$afterJournal.state -cne 'RolledBack') {
            throw 'The terminal RolledBack journal no longer matches the stale Activating status.'
        }
    }
    finally {
        if ($maintenanceMutexHeld) { $maintenanceMutex.ReleaseMutex() }
        $maintenanceMutex.Dispose()
    }
}

function Assert-InstalledState {
    param(
        [Parameter(Mandatory)][string] $ExpectedFingerprint,
        [Parameter(Mandatory)][string] $ExpectedHelperHash,
        [Parameter(Mandatory)][string] $ExpectedGuardianHash
    )
    $service = Get-CimInstance Win32_Service -Filter "Name='TreadmillRunnerGateway'" -ErrorAction Stop
    if ($null -eq $service -or $service.State -ne 'Running' -or $service.StartMode -ne 'Auto') {
        throw 'The gateway service is not Running with Automatic start.'
    }
    $expectedExecutable = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot "releases\$ExpectedVersion\TreadmillRunner.Gateway.exe"))
    $actualExecutable = [System.IO.Path]::GetFullPath((Get-ServiceExecutablePath -ImagePath ([string]$service.PathName)))
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualExecutable, $expectedExecutable) -or
        -not (Test-Path -LiteralPath $expectedExecutable -PathType Leaf)) {
        throw "The service executable is not the immutable expected release path: $actualExecutable"
    }
    $ready = Invoke-WebRequest -Method Get -Uri ($GatewayUrl.TrimEnd('/') + '/health/ready') -UseBasicParsing -TimeoutSec 10
    if ($ready.StatusCode -ne 200) { throw 'The installed gateway readiness endpoint did not return HTTP 200.' }
    $system = Invoke-GetJson '/api/system/version'
    if ([string]$system.releaseVersion -ne $ExpectedVersion -or [string]$system.buildFingerprint -ne $ExpectedFingerprint) {
        throw "Installed version/fingerprint mismatch: $($system.releaseVersion)/$($system.buildFingerprint)."
    }
    $status = Invoke-GetJson '/api/updates/status'
    if ([string]$status.currentVersion -ne $ExpectedVersion -or [string]$status.state -notin @('Current', 'Activated')) {
        throw "Installed update state is not terminal for ${ExpectedVersion}: $($status.state)/$($status.currentVersion)."
    }
    Assert-Idle
    $protectedHelper = Join-Path $InstallRoot 'updater\update-helper.ps1'
    $protectedGuardian = Join-Path $InstallRoot 'updater\service-guardian.ps1'
    if ((Get-Sha256Hex -Path $protectedHelper) -ne $ExpectedHelperHash -or
        (Get-Sha256Hex -Path $protectedGuardian) -ne $ExpectedGuardianHash) {
        throw 'The protected updater scripts do not match the verified release package.'
    }
    return [pscustomobject]@{
        ServiceState = [string]$service.State
        ServiceStartMode = [string]$service.StartMode
        ServiceExecutable = $actualExecutable
        ReadyStatus = [int]$ready.StatusCode
        ReleaseVersion = [string]$system.releaseVersion
        BuildFingerprint = [string]$system.buildFingerprint
        UpdateState = [string]$status.state
        ActiveSessionStatus = 204
        ProtectedHelperHash = $ExpectedHelperHash
        ProtectedGuardianHash = $ExpectedGuardianHash
    }
}

if ($ExpectedRelease -cne "v$ExpectedVersion") { throw 'ExpectedRelease must exactly equal v<ExpectedVersion>.' }
if ($DryRun -and $Activate) { throw '-DryRun cannot be combined with -Activate.' }
if ($Activate -and $Confirmation -cne 'ACTIVATE') { throw 'Activation requires literal -Confirmation ACTIVATE.' }
if (-not $Activate -and $null -ne $Confirmation) { throw '-Confirmation is accepted only with -Activate.' }
if ($null -eq (Get-Command gh -ErrorAction SilentlyContinue)) { throw 'GitHub CLI (gh) is required.' }

$gatewayUri = [Uri]$GatewayUrl
if (-not $gatewayUri.IsLoopback -or $gatewayUri.Scheme -ne 'http') { throw 'GatewayUrl must be loopback HTTP.' }
$resolvedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("TreadmillRunner.Deployment-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
try {
    $release = Invoke-GhJson -Arguments @('release', 'view', $ExpectedRelease, '--repo', $Repository, '--json', 'tagName,isDraft,isPrerelease,assets')
    if ([string]$release.tagName -cne $ExpectedRelease -or [bool]$release.isDraft -or [bool]$release.isPrerelease) {
        throw 'The requested GitHub release is not the exact published stable release.'
    }
    $remoteCommit = ((& gh api "repos/$Repository/commits/$ExpectedRelease" --jq '.sha') -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or $remoteCommit -cne $ExpectedCommit.ToLowerInvariant()) {
        throw "GitHub release $ExpectedRelease does not resolve to expected commit $ExpectedCommit."
    }

    $manifestName = 'stable.manifest.json'
    $packageName = "treadmillrunner-$ExpectedVersion-win-x64.zip"
    $offlineName = "treadmillrunner-$ExpectedVersion-offline-update.zip"
    $certificateName = 'treadmillrunner-release-signing.cer'
    $installerName = "TreadmillRunner-$ExpectedVersion-Windows-x64.zip"
    $expectedAssetNames = @($manifestName, $packageName, $offlineName, $certificateName, $installerName, 'SHA256SUMS.txt')
    $actualAssetNames = @($release.assets | ForEach-Object { [string]$_.name })
    if ($actualAssetNames.Count -ne $expectedAssetNames.Count -or
        @($actualAssetNames | Where-Object { $expectedAssetNames -notcontains $_ }).Count -ne 0) {
        throw 'The GitHub release asset set is not the exact signed release contract.'
    }

    & gh release download $ExpectedRelease --repo $Repository --dir $workRoot
    if ($LASTEXITCODE -ne 0) { throw 'The exact GitHub release assets could not be downloaded.' }
    foreach ($name in $expectedAssetNames) {
        if (-not (Test-Path -LiteralPath (Join-Path $workRoot $name) -PathType Leaf)) { throw "Downloaded release asset is missing: $name" }
    }

    $checksumLines = @(Get-Content -LiteralPath (Join-Path $workRoot 'SHA256SUMS.txt'))
    $checksums = @{}
    foreach ($line in $checksumLines) {
        if ($line -notmatch '^([0-9A-Fa-f]{64})\s{2}(.+)$' -or $checksums.ContainsKey($Matches[2])) { throw 'SHA256SUMS.txt is malformed or contains duplicate asset names.' }
        $checksums[$Matches[2]] = $Matches[1].ToLowerInvariant()
    }
    foreach ($name in $expectedAssetNames | Where-Object { $_ -ne 'SHA256SUMS.txt' }) {
        if (-not $checksums.ContainsKey($name) -or (Get-Sha256Hex -Path (Join-Path $workRoot $name)) -cne $checksums[$name]) {
            throw "Signed asset checksum mismatch: $name"
        }
    }

    $manifestPath = Join-Path $workRoot $manifestName
    $packagePath = Join-Path $workRoot $packageName
    $certificatePath = Join-Path $workRoot $certificateName
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ([int]$manifest.schemaVersion -ne 1 -or [string]$manifest.channel -cne 'stable' -or
        [string]$manifest.version -cne $ExpectedVersion -or [string]$manifest.packageFileName -cne $packageName -or
        ([string]$manifest.packageSha256).ToLowerInvariant() -cne (Get-Sha256Hex -Path $packagePath)) {
        throw 'The signed stable manifest does not match the expected release package.'
    }
    $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certificatePath)
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
    try {
        $notes = ([string]$manifest.releaseNotes).Replace("`r`n", "`n").Replace("`r", "`n")
        $payload = @([string]$manifest.schemaVersion, [string]$manifest.version, [string]$manifest.channel, [string]$manifest.packageFileName, ([string]$manifest.packageSha256).ToUpperInvariant(),
            [string]$manifest.minimumDatabaseSchemaVersion, [string]$manifest.maximumDatabaseSchemaVersion, $notes) -join "`n"
        if ($null -eq $rsa -or -not $rsa.VerifyData([System.Text.Encoding]::UTF8.GetBytes($payload),
            [Convert]::FromBase64String([string]$manifest.signature), [System.Security.Cryptography.HashAlgorithmName]::SHA256,
            [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)) { throw 'The signed stable manifest signature is invalid.' }
    }
    finally { if ($null -ne $rsa) { $rsa.Dispose() }; $certificate.Dispose() }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($packagePath)
    try {
        foreach ($required in @('TreadmillRunner.Gateway.exe', 'TreadmillRunner.Migrations.exe', 'Updates/update-helper.ps1', 'Updates/service-guardian.ps1', 'build-metadata.json')) {
            if ($null -eq $archive.GetEntry($required)) { throw "Signed package is missing $required." }
        }
        $metadataInput = $archive.GetEntry('build-metadata.json').Open()
        try { $metadata = (New-Object System.IO.StreamReader($metadataInput)).ReadToEnd() | ConvertFrom-Json }
        finally { $metadataInput.Dispose() }
        if ([string]$metadata.version -cne $ExpectedVersion -or [string]$metadata.sourceRevision -cne $ExpectedCommit.ToLowerInvariant()) {
            throw 'Signed package build provenance does not match the exact expected commit.'
        }
        $buildId = [string]$metadata.buildId
        $buildSha = [System.Security.Cryptography.SHA256]::Create()
        try { $expectedFingerprint = ([System.BitConverter]::ToString($buildSha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($buildId)))).Replace('-', '').Substring(0, 16).ToLowerInvariant() }
        finally { $buildSha.Dispose() }
        $helperHash = Get-ZipEntrySha256 -Archive $archive -Name 'Updates/update-helper.ps1'
        $guardianHash = Get-ZipEntrySha256 -Archive $archive -Name 'Updates/service-guardian.ps1'
    }
    finally { $archive.Dispose() }

    if (-not $DryRun) {
        # Normalize a verified terminal session before the GET-only physical
        # preflight; dry-run remains strictly read-only and still requires 204.
        Assert-Idle
    }

    # This is a GET-only, no-command preflight. It is intentionally required
    # before any update check/stage/activation request.
    & (Join-Path $PSScriptRoot 'physical-acceptance-preflight.ps1') `
        -GatewayUrl $GatewayUrl `
        -ExpectedTreadmillModel $ExpectedTreadmillModel `
        -ExpectedTreadmillFirmware $ExpectedTreadmillFirmware `
        -ExpectedHeartRateDisplayName $ExpectedHeartRateDisplayName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'The read-only deployment preflight failed.' }

    $beforeProfiles = @(Expand-JsonArray -Value (Invoke-GetJson '/api/planning/profiles'))
    $beforeProfileIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $beforeHistory = @{}
    foreach ($profile in $beforeProfiles) {
        $profileId = [string]$profile.id
        if ($profileId -match '^[0-9a-fA-F-]{36}$') {
            if (-not $beforeProfileIds.Add($profileId)) { throw "The profile snapshot contains duplicate ID $profileId." }
            $beforeHistory[$profileId] = @(Expand-JsonArray -Value (Invoke-GetJson "/api/history?profileId=$([Uri]::EscapeDataString($profileId))&take=5000"))
        }
    }

    if (-not $DryRun) {
        # The currently installed helper cannot load code from the newly
        # published ZIP until its protected infrastructure is refreshed. Use
        # the already signature/hash-verified package to bootstrap exactly the
        # four installer-required entries before feed publication or any update
        # API request.
        $currentRelease = Get-CurrentInstalledRelease
        $repairRoot = Join-Path $workRoot 'protected-infrastructure-repair'
        Repair-ProtectedInfrastructure `
            -PackagePath $packagePath `
            -CertificatePath $certificatePath `
            -ExpectedHelperHash $helperHash `
            -ExpectedGuardianHash $guardianHash `
            -CurrentVersion ([string]$currentRelease.Version) `
            -RepairRoot $repairRoot
        Invoke-PendingActivationReconciliation `
            -ResolvedInstallRoot $resolvedInstallRoot `
            -ResolvedDataRoot $resolvedDataRoot
        Invoke-StaleActivatingCoordinatorNormalization `
            -ResolvedInstallRoot $resolvedInstallRoot `
            -ResolvedDataRoot $resolvedDataRoot `
            -ExpectedCurrentVersion ([string]$currentRelease.Version) `
            -ExpectedCurrentExecutable ([string]$currentRelease.Executable)

        & (Join-Path $PSScriptRoot 'install-stable-update-feed.ps1') `
            -Version $ExpectedVersion `
            -SourceFeed $workRoot `
            -PublicCertificatePath $certificatePath `
            -InstallRoot $resolvedInstallRoot `
            -DataRoot $resolvedDataRoot
        if ($LASTEXITCODE -ne 0) { throw 'The verified GitHub feed could not be installed.' }

        $check = Invoke-RestMethod -Method Post -Uri ($GatewayUrl.TrimEnd('/') + '/api/updates/check') -TimeoutSec 15
        if ([string]$check.availableVersion -ne $ExpectedVersion) { throw "Update check did not expose exact version $ExpectedVersion." }
        $status = Invoke-GetJson '/api/updates/status'
        if ([string]$status.state -ne 'Staged' -or [string]$status.stagedVersion -ne $ExpectedVersion) {
            $stageBody = @{ expectedVersion = $ExpectedVersion } | ConvertTo-Json -Compress
            $stage = Invoke-RestMethod -Method Post -Uri ($GatewayUrl.TrimEnd('/') + '/api/updates/stage') -ContentType 'application/json' -Body $stageBody -TimeoutSec 30
            if ([string]$stage.stagedVersion -ne $ExpectedVersion) { throw 'Exact expected version was not staged.' }
            $status = Invoke-GetJson '/api/updates/status'
            if ([string]$status.state -ne 'Staged' -or [string]$status.stagedVersion -ne $ExpectedVersion) {
                throw 'The update status did not confirm the exact expected staged version.'
            }
        }
        if ($Activate) {
            # Recheck the idle gate directly adjacent to activation. No other
            # request is allowed to stand in for this HTTP 204 observation.
            Assert-Idle
            $activateBody = @{ confirmation = 'ACTIVATE'; expectedVersion = $ExpectedVersion } | ConvertTo-Json -Compress
            Invoke-RestMethod -Method Post -Uri ($GatewayUrl.TrimEnd('/') + '/api/updates/activate') -ContentType 'application/json' -Body $activateBody -TimeoutSec 30 | Out-Null
            $deadline = [DateTimeOffset]::UtcNow.AddSeconds(180)
            do {
                Start-Sleep -Seconds 3
                try {
                    $installed = Assert-InstalledState -ExpectedFingerprint $expectedFingerprint -ExpectedHelperHash $helperHash -ExpectedGuardianHash $guardianHash
                    break
                }
                catch {
                    if ([DateTimeOffset]::UtcNow -ge $deadline) { throw }
                }
            } while ([DateTimeOffset]::UtcNow -lt $deadline)
            $afterProfiles = @(Expand-JsonArray -Value (Invoke-GetJson '/api/planning/profiles'))
            if ($afterProfiles.Count -ne $beforeProfiles.Count) {
                throw "The profile count changed across activation ($($beforeProfiles.Count) -> $($afterProfiles.Count))."
            }
            foreach ($profile in $beforeProfiles) {
                $matchingProfiles = @($afterProfiles | Where-Object { [string]$_.id -eq [string]$profile.id })
                if ($matchingProfiles.Count -ne 1) {
                    throw "Profile $($profile.id) was not retained across activation."
                }
                if ((Convert-ToCanonicalJson -Value $matchingProfiles[0]) -cne (Convert-ToCanonicalJson -Value $profile)) {
                    throw "Profile $($profile.id) payload changed across activation."
                }
                $afterHistory = @(Expand-JsonArray -Value (Invoke-GetJson "/api/history?profileId=$([Uri]::EscapeDataString([string]$profile.id))&take=5000"))
                $beforeHistoryItems = @($beforeHistory[[string]$profile.id])
                if ($afterHistory.Count -ne $beforeHistoryItems.Count) {
                    throw "History count for profile $($profile.id) changed across activation ($($beforeHistoryItems.Count) -> $($afterHistory.Count))."
                }
                $afterHistoryById = @{}
                foreach ($historyItem in $afterHistory) {
                    $historyId = [string]$historyItem.id
                    if ([string]::IsNullOrWhiteSpace($historyId) -or $afterHistoryById.ContainsKey($historyId)) {
                        throw "History for profile $($profile.id) contains a missing or duplicate ID."
                    }
                    $afterHistoryById[$historyId] = $historyItem
                }
                foreach ($historyItem in $beforeHistoryItems) {
                    $historyId = [string]$historyItem.id
                    if (-not $afterHistoryById.ContainsKey($historyId)) {
                        throw "History item $historyId for profile $($profile.id) was not retained across activation."
                    }
                    if ((Convert-ToCanonicalJson -Value $afterHistoryById[$historyId]) -cne (Convert-ToCanonicalJson -Value $historyItem)) {
                        throw "History item $historyId for profile $($profile.id) payload changed across activation."
                    }
                }
            }
        }
    }

    [pscustomobject]@{
        Mode = if ($DryRun) { 'DryRun' } elseif ($Activate) { 'Activated' } else { 'Staged' }
        ExpectedVersion = $ExpectedVersion
        ExpectedCommit = $ExpectedCommit.ToLowerInvariant()
        ExpectedRelease = $ExpectedRelease
        ExpectedBuildFingerprint = $expectedFingerprint
        PackageSha256 = (Get-Sha256Hex -Path $packagePath)
        ProtectedHelperSha256 = $helperHash
        ProtectedGuardianSha256 = $guardianHash
        FeedInstalled = -not $DryRun
        Activated = $Activate -and -not $DryRun
    }
}
finally {
    if (Test-Path -LiteralPath $workRoot) { Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
