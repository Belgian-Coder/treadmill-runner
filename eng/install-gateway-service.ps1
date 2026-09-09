#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][string] $ReleasePath,
    [Parameter(Mandatory)][string] $PublicCertificatePath,
    [string] $SourceDatabasePath,
    [string] $InstallRoot = "$env:ProgramFiles\TreadmillRunner",
    [string] $DataRoot = "$env:ProgramData\TreadmillRunner",
    [switch] $RepairUpdateInfrastructureOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$serviceName = 'TreadmillRunnerGateway'
$taskName = 'TreadmillRunnerUpdate'
$guardianTaskName = 'TreadmillRunnerGuardian'
$serviceDiagnosticLog = 'Microsoft-Windows-Services/Diagnostic'
if ($RepairUpdateInfrastructureOnly) {
    # Enumerating the root folder lets a missing task be distinguished from an
    # inspection failure. Repair must not replace either protected script while
    # its scheduled task may still be executing it.
    $protectedTasks = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object {
        $_.TaskName -in @($taskName, $guardianTaskName)
    })
    $runningProtectedTask = $protectedTasks | Where-Object { $_.State -eq 'Running' } | Select-Object -First 1
    if ($null -ne $runningProtectedTask) {
        throw "The $($runningProtectedTask.TaskName) task is running; protected update infrastructure cannot be repaired now."
    }
}
$resolvedRelease = [System.IO.Path]::GetFullPath($ReleasePath)
$resolvedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$resolvedCertificate = [System.IO.Path]::GetFullPath($PublicCertificatePath)
if (-not (Test-Path -LiteralPath $resolvedRelease -PathType Container)) { throw 'ReleasePath is missing.' }
if (-not (Test-Path -LiteralPath $resolvedCertificate -PathType Leaf)) { throw 'The public signing certificate is missing.' }
if ((Get-Item -LiteralPath $resolvedRelease).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw 'ReleasePath cannot be a reparse point.'
}
foreach ($required in @('TreadmillRunner.Gateway.exe', 'TreadmillRunner.Migrations.exe', 'Updates\update-helper.ps1', 'Updates\service-guardian.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedRelease $required) -PathType Leaf)) {
        throw "ReleasePath is missing $required."
    }
}

$releaseRoot = Join-Path $resolvedInstallRoot 'releases'
$targetRelease = Join-Path $releaseRoot $Version
$updaterRoot = Join-Path $resolvedInstallRoot 'updater'
$databasePath = Join-Path $resolvedDataRoot 'data\treadmillrunner.db'
$dataProtectionKeyPath = Join-Path $resolvedDataRoot 'data\keys'
$backupRoot = Join-Path $resolvedDataRoot 'backups'
$feedRoot = Join-Path $resolvedDataRoot 'updates\feed'
$stagingRoot = Join-Path $resolvedDataRoot 'updates\staging'
$planRoot = Join-Path $resolvedDataRoot 'updates\plans'
$certificateTarget = Join-Path $updaterRoot 'signing.cer'
$helperTarget = Join-Path $updaterRoot 'update-helper.ps1'
$guardianTarget = Join-Path $updaterRoot 'service-guardian.ps1'
$executableTarget = Join-Path $targetRelease 'TreadmillRunner.Gateway.exe'
$maintenanceMarkerPath = Join-Path $resolvedDataRoot 'updates\service-maintenance.lock'
$targetReleaseOwned = $false
$serviceInstallStarted = $false
$serviceCreatedByTransaction = $false
$serviceConfigurationChanged = $false
$serviceWasRunning = $false
$previousImagePath = $null
$updateTaskCreatedByTransaction = $false
$guardianTaskCreatedByTransaction = $false
$updateTaskRegistrationCompleted = $false
$guardianTaskRegistrationCompleted = $false
$updateTaskExistedBefore = $false
$guardianTaskExistedBefore = $false
$updateTaskPreviousXml = $null
$guardianTaskPreviousXml = $null
$updateTaskRegisteredExecute = $null
$updateTaskRegisteredArguments = $null
$updateTaskRegisteredUserId = $null
$updateTaskRegisteredLogonType = $null
$updateTaskRegisteredRunLevel = $null
$guardianTaskRegisteredExecute = $null
$guardianTaskRegisteredArguments = $null
$guardianTaskRegisteredUserId = $null
$guardianTaskRegisteredLogonType = $null
$guardianTaskRegisteredRunLevel = $null
$serviceEnvironmentCaptured = $false
$serviceEnvironmentExistedBefore = $false
$previousServiceEnvironment = @()
$directoryAclStates = @()
$installerProtectedFileStates = [ordered]@{
    helper = [ordered]@{ key = 'helper'; targetPath = $helperTarget; existedBefore = $false; changed = $false; backupPath = $null; backupSha256 = $null; sourceSha256 = $null; targetSha256 = $null }
    guardian = [ordered]@{ key = 'guardian'; targetPath = $guardianTarget; existedBefore = $false; changed = $false; backupPath = $null; backupSha256 = $null; sourceSha256 = $null; targetSha256 = $null }
    certificate = [ordered]@{ key = 'certificate'; targetPath = $certificateTarget; existedBefore = $false; changed = $false; backupPath = $null; backupSha256 = $null; sourceSha256 = $null; targetSha256 = $null }
}

function Get-Sha256Hex {
    param([Parameter(Mandatory)][string]$Path)
    return ([string](Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash).ToLowerInvariant()
}

function Get-ExistingGatewayService {
    $matches = @(Get-Service -ErrorAction Stop | Where-Object {
        [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$_.Name, $serviceName)
    })
    if ($matches.Count -gt 1) { throw 'Gateway service inspection returned an ambiguous result.' }
    return $matches | Select-Object -First 1
}

function Get-ExistingRootScheduledTask {
    param([Parameter(Mandatory)][string]$Name)
    $matches = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop | Where-Object {
        [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$_.TaskName, $Name) -and
        [System.StringComparer]::Ordinal.Equals([string]$_.TaskPath, '\')
    })
    if ($matches.Count -gt 1) { throw "Scheduled task inspection returned an ambiguous result for $Name." }
    return $matches | Select-Object -First 1
}

function Assert-InstallerTaskMatchesRegistration {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$ExpectedExecute,
        [string]$ExpectedArguments,
        [string]$ExpectedUserId,
        [string]$ExpectedLogonType,
        [string]$ExpectedRunLevel
    )
    if ([string]::IsNullOrWhiteSpace($ExpectedExecute) -or
        [string]::IsNullOrWhiteSpace($ExpectedArguments) -or
        [string]::IsNullOrWhiteSpace($ExpectedUserId) -or
        [string]::IsNullOrWhiteSpace($ExpectedLogonType) -or
        [string]::IsNullOrWhiteSpace($ExpectedRunLevel)) {
        throw "The transaction has no durable registration contract for scheduled task $Name."
    }
    if (-not (Test-InstallerTaskMatchesRegistration -Name $Name -ExpectedExecute $ExpectedExecute -ExpectedArguments $ExpectedArguments `
            -ExpectedUserId $ExpectedUserId -ExpectedLogonType $ExpectedLogonType -ExpectedRunLevel $ExpectedRunLevel)) {
        throw "Scheduled task $Name no longer matches this installer's registered action or SYSTEM/ServiceAccount/Highest principal."
    }
}

function Test-InstallerTaskMatchesRegistration {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$ExpectedExecute,
        [string]$ExpectedArguments,
        [string]$ExpectedUserId,
        [string]$ExpectedLogonType,
        [string]$ExpectedRunLevel
    )
    if ([string]::IsNullOrWhiteSpace($ExpectedExecute) -or
        [string]::IsNullOrWhiteSpace($ExpectedArguments) -or
        [string]::IsNullOrWhiteSpace($ExpectedUserId) -or
        [string]::IsNullOrWhiteSpace($ExpectedLogonType) -or
        [string]::IsNullOrWhiteSpace($ExpectedRunLevel)) {
        throw "The transaction has no durable registration contract for scheduled task $Name."
    }
    $task = Get-ExistingRootScheduledTask -Name $Name
    if ($null -eq $task) { return $false }
    $actions = @($task.Actions)
    $principal = $task.Principal
    if ($actions.Count -ne 1 -or $null -eq $principal) { return $false }
    return [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$actions[0].Execute, $ExpectedExecute) -and
        [System.StringComparer]::Ordinal.Equals([string]$actions[0].Arguments, $ExpectedArguments) -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$principal.UserId, $ExpectedUserId) -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$principal.LogonType, $ExpectedLogonType) -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$principal.RunLevel, $ExpectedRunLevel)
}

function Restore-InstallerScheduledTask {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][bool]$RegistrationCompleted,
        [Parameter(Mandatory)][bool]$ExistedBefore,
        [string]$PreviousXml,
        [string]$ExpectedExecute,
        [string]$ExpectedArguments,
        [string]$ExpectedUserId,
        [string]$ExpectedLogonType,
        [string]$ExpectedRunLevel
    )
    $task = Get-ExistingRootScheduledTask -Name $Name
    $currentXml = if ($null -ne $task) { (Export-ScheduledTask -TaskName $Name -TaskPath '\' -ErrorAction Stop | Out-String).Trim() } else { $null }
    # Recovery is idempotent after the compensating mutation itself completed
    # but before marker/state cleanup. Do not reassert ownership of a task that
    # already equals the captured prior XML, or of an absent task that did not
    # exist before this transaction.
    if ($RegistrationCompleted -and
        ((-not $ExistedBefore -and $null -eq $task) -or
         ($ExistedBefore -and -not [string]::IsNullOrWhiteSpace($PreviousXml) -and
          [System.StringComparer]::Ordinal.Equals($currentXml, $PreviousXml)))) { return }
    $hasRegistrationContract = -not ([string]::IsNullOrWhiteSpace($ExpectedExecute) -or
        [string]::IsNullOrWhiteSpace($ExpectedArguments) -or [string]::IsNullOrWhiteSpace($ExpectedUserId) -or
        [string]::IsNullOrWhiteSpace($ExpectedLogonType) -or [string]::IsNullOrWhiteSpace($ExpectedRunLevel))
    if (-not $hasRegistrationContract) {
        if (-not $RegistrationCompleted -and $null -eq $task -and -not $ExistedBefore) { return }
        if (-not $RegistrationCompleted -and $ExistedBefore -and -not [string]::IsNullOrWhiteSpace($PreviousXml) -and
            [System.StringComparer]::Ordinal.Equals($currentXml, $PreviousXml)) { return }
        throw "Scheduled task $Name has no durable registration contract; refusing rollback."
    }
    $registeredMatch = Test-InstallerTaskMatchesRegistration -Name $Name -ExpectedExecute $ExpectedExecute -ExpectedArguments $ExpectedArguments `
        -ExpectedUserId $ExpectedUserId -ExpectedLogonType $ExpectedLogonType -ExpectedRunLevel $ExpectedRunLevel
    if (-not $RegistrationCompleted -and -not $registeredMatch) {
        if ($null -eq $task -and -not $ExistedBefore) { return }
        if ($ExistedBefore -and -not [string]::IsNullOrWhiteSpace($PreviousXml) -and
            [System.StringComparer]::Ordinal.Equals($currentXml, $PreviousXml)) { return }
        throw "Scheduled task $Name is neither the prior task nor the exact transaction registration; refusing rollback."
    }
    # A crash before this durable bit may still have completed Register-ScheduledTask.
    # Restore only when the current task proves exact ownership by this transaction.
    Assert-InstallerTaskMatchesRegistration -Name $Name -ExpectedExecute $ExpectedExecute -ExpectedArguments $ExpectedArguments `
        -ExpectedUserId $ExpectedUserId -ExpectedLogonType $ExpectedLogonType -ExpectedRunLevel $ExpectedRunLevel
    if ($ExistedBefore) {
        if ([string]::IsNullOrWhiteSpace($PreviousXml)) { throw "The previous XML for scheduled task $Name is missing." }
        Register-ScheduledTask -TaskName $Name -Xml $PreviousXml -Force -ErrorAction Stop | Out-Null
    }
    else {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop
    }
}

function Get-InstallerServiceExecutablePath {
    param([Parameter(Mandatory)][string]$ImagePath)
    if ($ImagePath -match '^\s*"([^"]+)"\s*$') { return [System.IO.Path]::GetFullPath($Matches[1]) }
    if ($ImagePath -match '^\s*(\S+)\s*$') { return [System.IO.Path]::GetFullPath($Matches[1]) }
    throw 'The gateway service image path is not an exact executable path.'
}

function Restore-InstallerServiceImageSafely {
    param(
        [Parameter(Mandatory)][string]$PreviousImagePath,
        [Parameter(Mandatory)][string]$TargetImagePath
    )
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    if ($null -eq $service) { throw 'The gateway service is missing during image rollback.' }
    $actualExecutable = Get-InstallerServiceExecutablePath -ImagePath ([string]$service.PathName)
    $targetExecutable = Get-InstallerServiceExecutablePath -ImagePath $TargetImagePath
    $previousExecutable = Get-InstallerServiceExecutablePath -ImagePath $PreviousImagePath
    if ([System.StringComparer]::OrdinalIgnoreCase.Equals($actualExecutable, $previousExecutable)) {
        # Another recovery pass already restored the previous image. Do not
        # rewrite it, because the transaction no longer owns that transition.
        return
    }
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualExecutable, $targetExecutable)) {
        throw 'The current gateway service image changed outside this installer transaction; refusing to restore the previous image.'
    }
    & sc.exe config $serviceName 'binPath=' $PreviousImagePath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'The previous gateway service image could not be restored.' }
    $restoredService = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    $restoredExecutable = Get-InstallerServiceExecutablePath -ImagePath ([string]$restoredService.PathName)
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($restoredExecutable, $previousExecutable)) {
        throw 'The restored gateway service image does not match the transaction snapshot.'
    }
}

function Copy-DurableFile {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    $temporary = "$Destination.write-tmp"
    $sourceStream = [System.IO.File]::Open($Source, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $destinationStream = [System.IO.FileStream]::new(
        $temporary, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None, 65536, [System.IO.FileOptions]::WriteThrough)
    try {
        $sourceStream.CopyTo($destinationStream)
        $destinationStream.Flush($true)
    }
    finally {
        $destinationStream.Dispose()
        $sourceStream.Dispose()
    }
    if ([System.IO.File]::Exists($Destination)) { [System.IO.File]::Replace($temporary, $Destination, $null, $true) }
    else { [System.IO.File]::Move($temporary, $Destination) }
}
$databaseCreatedByTransaction = $false
$migrationBackupPath = $null
$migrationBackupCreated = $false
$migrationBackupSha256 = $null
$installationCommitted = $false
$installerTransactionId = [Guid]::NewGuid().ToString('N')
$installerStatePath = Join-Path $planRoot ".installer-$installerTransactionId.json"
$installerProcess = Get-Process -Id $PID -ErrorAction Stop
$installerProcessStartTimeUtc = $installerProcess.StartTime.ToUniversalTime().ToString('O')
$installerProcessPath = [string]$installerProcess.Path

function Wait-MaintenanceMutex {
    param(
        [Parameter(Mandatory)][System.Threading.Mutex]$Mutex,
        [int]$TimeoutMilliseconds = 30000
    )
    try {
        return $Mutex.WaitOne($TimeoutMilliseconds)
    }
    catch [System.Threading.AbandonedMutexException] {
        # WaitOne throws after transferring ownership when the previous owner died.
        return $true
    }
}

function Write-InstallerState {
    param(
        [Parameter(Mandatory)][string]$Phase,
        [bool]$TargetOwned = $targetReleaseOwned,
        [bool]$ServiceCreated = $serviceCreatedByTransaction,
        [bool]$ServiceConfigurationChanged = $serviceConfigurationChanged,
        [bool]$DatabaseCreated = $databaseCreatedByTransaction,
        [string]$PreviousImage = $previousImagePath,
        [bool]$WasRunning = $serviceWasRunning,
        [bool]$UpdateTaskCreated = $updateTaskCreatedByTransaction,
        [bool]$GuardianTaskCreated = $guardianTaskCreatedByTransaction,
        [bool]$UpdateTaskExisted = $updateTaskExistedBefore,
        [string]$UpdateTaskXml = $updateTaskPreviousXml,
        [bool]$GuardianTaskExisted = $guardianTaskExistedBefore,
        [string]$GuardianTaskXml = $guardianTaskPreviousXml
    )
    $state = [ordered]@{
        schemaVersion = 1
        transactionId = $installerTransactionId
        version = $Version
        installRoot = $resolvedInstallRoot
        dataRoot = $resolvedDataRoot
        processId = $PID
        processStartTimeUtc = $installerProcessStartTimeUtc
        processPath = $installerProcessPath
        phase = $Phase
        targetOwned = $TargetOwned
        serviceCreated = $ServiceCreated
        serviceConfigurationChanged = $ServiceConfigurationChanged
        databaseCreated = $DatabaseCreated
        previousImagePath = $PreviousImage
        serviceWasRunning = $WasRunning
        updateTaskCreated = $UpdateTaskCreated
        updateTaskRegistrationCompleted = $updateTaskRegistrationCompleted
        updateTaskRegisteredExecute = $updateTaskRegisteredExecute
        updateTaskRegisteredArguments = $updateTaskRegisteredArguments
        updateTaskRegisteredUserId = $updateTaskRegisteredUserId
        updateTaskRegisteredLogonType = $updateTaskRegisteredLogonType
        updateTaskRegisteredRunLevel = $updateTaskRegisteredRunLevel
        guardianTaskCreated = $GuardianTaskCreated
        guardianTaskRegistrationCompleted = $guardianTaskRegistrationCompleted
        guardianTaskRegisteredExecute = $guardianTaskRegisteredExecute
        guardianTaskRegisteredArguments = $guardianTaskRegisteredArguments
        guardianTaskRegisteredUserId = $guardianTaskRegisteredUserId
        guardianTaskRegisteredLogonType = $guardianTaskRegisteredLogonType
        guardianTaskRegisteredRunLevel = $guardianTaskRegisteredRunLevel
        updateTaskExistedBefore = $UpdateTaskExisted
        updateTaskPreviousXml = $UpdateTaskXml
        guardianTaskExistedBefore = $GuardianTaskExisted
        guardianTaskPreviousXml = $GuardianTaskXml
        serviceEnvironmentCaptured = $serviceEnvironmentCaptured
        serviceEnvironmentExistedBefore = $serviceEnvironmentExistedBefore
        previousServiceEnvironment = @($previousServiceEnvironment)
        directoryAclStates = @($directoryAclStates)
        migrationBackupPath = $migrationBackupPath
        migrationBackupCreated = $migrationBackupCreated
        migrationBackupSha256 = $migrationBackupSha256
        protectedFiles = @($installerProtectedFileStates.Values)
        updatedAtUtc = [DateTimeOffset]::UtcNow.ToString('O')
    }
    $temporary = "$installerStatePath.tmp"
    $payload = [System.Text.UTF8Encoding]::new($false).GetBytes(($state | ConvertTo-Json -Depth 20 -Compress))
    $stream = [System.IO.FileStream]::new($temporary, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None, 4096, [System.IO.FileOptions]::WriteThrough)
    try {
        $stream.Write($payload, 0, $payload.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
    if ([System.IO.File]::Exists($installerStatePath)) {
        [System.IO.File]::Replace($temporary, $installerStatePath, $null, $true)
    }
    else {
        [System.IO.File]::Move($temporary, $installerStatePath)
    }
}

function Restore-InstallerProtectedFiles {
    param([Parameter(Mandatory)]$State)
    $stateTransactionId = [string]$State.transactionId
    if ($stateTransactionId -notmatch '^[0-9a-f]{32}$') { throw 'The installer protected-file state has an invalid transaction id.' }
    foreach ($record in @($State.protectedFiles)) {
        $key = [string]$record.key
        if (-not $installerProtectedFileStates.Contains($key)) { throw "The installer state contains an unknown protected file key: $key." }
        if (-not ($record.PSObject.Properties.Name -contains 'changed')) { throw 'The installer protected-file state is incomplete.' }
        if (-not [bool]$record.changed) { continue }
        $expected = $installerProtectedFileStates[$key]
        if ([string]$record.targetPath -ne [string]$expected.targetPath) {
            throw "The installer state target for $key is outside the protected updater root."
        }
        $target = [string]$expected.targetPath
        if ([bool]$record.existedBefore) {
            $backup = [string]$record.backupPath
            if ([string]::IsNullOrWhiteSpace($backup)) { throw "The installer backup for $key is missing." }
            $resolvedBackup = [System.IO.Path]::GetFullPath($backup)
            if ($resolvedBackup -notlike (([System.IO.Path]::GetFullPath($planRoot)).TrimEnd('\') + '\.installer-' + $stateTransactionId + '-*')) {
                throw "The installer backup for $key is outside the transaction workspace."
            }
            if (-not (Test-Path -LiteralPath $resolvedBackup -PathType Leaf)) { throw "The installer backup for $key is missing." }
            if ([string]::IsNullOrWhiteSpace([string]$record.backupSha256) -or
                (Get-Sha256Hex -Path $resolvedBackup) -ne [string]$record.backupSha256) {
                throw "The installer backup for $key failed hash verification."
            }
            Copy-DurableFile -Source $resolvedBackup -Destination $target
            if ((Get-Sha256Hex -Path $target) -ne [string]$record.backupSha256) { throw "The installer restore for $key failed hash verification." }
        }
        elseif (Test-Path -LiteralPath $target -PathType Leaf) {
            Remove-Item -LiteralPath $target -Force
        }
    }
}

function Restore-InstallerMutableInfrastructure {
    param([Parameter(Mandatory)]$State)
    $serviceRegistryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName"
    if ([bool]$State.serviceEnvironmentCaptured) {
        if ([bool]$State.serviceEnvironmentExistedBefore) {
            New-ItemProperty -Path $serviceRegistryPath -Name Environment -PropertyType MultiString `
                -Value @($State.previousServiceEnvironment) -Force | Out-Null
        }
        else {
            Remove-ItemProperty -Path $serviceRegistryPath -Name Environment -Force -ErrorAction SilentlyContinue
        }
    }

    $allowedAclPaths = @(
        $resolvedDataRoot,
        $resolvedInstallRoot,
        (Join-Path $resolvedDataRoot 'updates'),
        $feedRoot,
        (Split-Path -Parent $databasePath),
        $dataProtectionKeyPath,
        $backupRoot,
        $stagingRoot,
        $planRoot
    ) | ForEach-Object { [System.IO.Path]::GetFullPath($_) }
    foreach ($record in @($State.directoryAclStates) | Sort-Object { ([string]$_.path).Length } -Descending) {
        $path = [System.IO.Path]::GetFullPath([string]$record.path)
        if ($path -notin $allowedAclPaths -or [string]::IsNullOrWhiteSpace([string]$record.sddl)) {
            throw 'The installer ACL recovery state is invalid.'
        }
        if (Test-Path -LiteralPath $path -PathType Container) {
            $acl = Get-Acl -LiteralPath $path -ErrorAction Stop
            $acl.SetSecurityDescriptorSddlForm([string]$record.sddl)
            Set-Acl -LiteralPath $path -AclObject $acl -ErrorAction Stop
        }
    }
}

function Set-PostCommitOperationalInfrastructure {
    # These settings are forward-compatible machine policy. Apply them only
    # after the release/database/service/task transaction is durably committed;
    # a later failure is repaired by rerunning the installer and never rolls a
    # healthy release back while leaving partial machine policy behind.
    & sc.exe failure $serviceName 'reset=' 86400 'actions=' restart/5000/restart/15000/restart/60000 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Windows Service recovery actions could not be configured.' }
    & sc.exe failureflag $serviceName 1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Windows Service non-crash recovery could not be configured.' }

    $diagnosticLog = Get-WinEvent -ListLog $serviceDiagnosticLog -ErrorAction Stop
    $diagnosticLogNeedsEnable = -not $diagnosticLog.IsEnabled
    if ($diagnosticLog.IsEnabled -and $diagnosticLog.MaximumSizeInBytes -ne 4194304) {
        & wevtutil.exe sl $serviceDiagnosticLog /e:false /q:true
        if ($LASTEXITCODE -ne 0) { throw 'The Windows service-control diagnostic log could not be paused for reconfiguration.' }
        $diagnosticLogNeedsEnable = $true
    }
    if ($diagnosticLog.MaximumSizeInBytes -ne 4194304) {
        & wevtutil.exe sl $serviceDiagnosticLog /ms:4194304 /q:true
        if ($LASTEXITCODE -ne 0) { throw 'The Windows service-control diagnostic log size could not be bounded.' }
    }
    if ($diagnosticLogNeedsEnable) {
        & wevtutil.exe sl $serviceDiagnosticLog /e:true /q:true
        if ($LASTEXITCODE -ne 0) { throw 'The bounded Windows service-control diagnostic log could not be enabled.' }
    }

    $firewallName = 'TreadmillRunner Private LAN'
    Remove-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort 5180 -Profile Private -RemoteAddress LocalSubnet | Out-Null
}

function Replace-InstallerProtectedFile {
    param(
        [Parameter(Mandatory)][ValidateSet('helper', 'guardian', 'certificate')][string]$Key,
        [Parameter(Mandatory)][string]$SourcePath
    )
    $record = $installerProtectedFileStates[$Key]
    $record.changed = $true
    $record.sourceSha256 = Get-Sha256Hex -Path $SourcePath
    if ($record.existedBefore) {
        $record.backupPath = Join-Path $planRoot ".installer-$installerTransactionId-$Key.bak"
        Copy-DurableFile -Source $record.targetPath -Destination $record.backupPath
        $record.backupSha256 = Get-Sha256Hex -Path $record.backupPath
    }
    # Persist ownership and the backup before replacing the live updater file.
    Write-InstallerState -Phase "ProtectedFile-$Key"
    Copy-DurableFile -Source $SourcePath -Destination $record.targetPath
    $record.targetSha256 = Get-Sha256Hex -Path $record.targetPath
    if ($record.targetSha256 -ne $record.sourceSha256) { throw "The protected updater file $Key failed hash verification." }
    Write-InstallerState -Phase "ProtectedFileCommitted-$Key"
}

function Remove-InstallerTransactionWorkspace {
    param([Parameter(Mandatory)][string]$TransactionId)
    foreach ($name in @('helper', 'guardian', 'certificate')) {
        foreach ($suffix in @('.bak', '.tmp')) {
            $path = Join-Path $planRoot ".installer-$TransactionId-$name$suffix"
            if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
        }
    }
}

function Initialize-InstallerProtectedFileState {
    foreach ($record in $installerProtectedFileStates.Values) {
        $record.existedBefore = Test-Path -LiteralPath $record.targetPath -PathType Leaf
    }
}

function Commit-InstallerMigrationBackup {
    if ($migrationBackupCreated) {
        # The caller durably writes the final Committed phase before entering
        # this cleanup. Until then this hashed snapshot remains the rollback
        # source for both in-process and stale-transaction recovery.
        if (-not (Test-Path -LiteralPath $migrationBackupPath -PathType Leaf) -or
            (Get-Sha256Hex -Path $migrationBackupPath) -ne $migrationBackupSha256) {
            throw 'The committed migration backup failed final hash verification.'
        }
        Remove-Item -LiteralPath $migrationBackupPath -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $migrationBackupPath) { throw 'The committed migration backup could not be removed.' }
        $script:migrationBackupCreated = $false
        $script:migrationBackupPath = $null
        $script:migrationBackupSha256 = $null
    }
}

function Remove-InstallerStateAndMarker {
    if (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf) {
        $marker = Get-Content -LiteralPath $maintenanceMarkerPath -Raw -ErrorAction SilentlyContinue
        if ($marker -notmatch "^installer $([regex]::Escape($Version)) $([regex]::Escape($installerTransactionId)) ") {
            throw 'The installer maintenance marker is no longer owned by this transaction.'
        }
        Remove-Item -LiteralPath $maintenanceMarkerPath -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf) { throw 'The installer maintenance marker could not be removed.' }
    }
    # Remove the marker first. A process interruption between these two
    # operations leaves recoverable state, but cannot wedge every retry behind
    # an orphaned ownership marker with no transaction state.
    if (Test-Path -LiteralPath $installerStatePath -PathType Leaf) { Remove-Item -LiteralPath $installerStatePath -Force -ErrorAction SilentlyContinue }
    Remove-InstallerTransactionWorkspace -TransactionId $installerTransactionId
    $migrationWorkspace = Join-Path $backupRoot ".installer-$installerTransactionId-migration.db"
    if (Test-Path -LiteralPath $migrationWorkspace -PathType Leaf) { Remove-Item -LiteralPath $migrationWorkspace -Force -ErrorAction SilentlyContinue }
}

function Remove-InstallerRecoveryState {
    param([Parameter(Mandatory)]$State)
    $stateTargetOwned = [bool]$State.targetOwned
    $stateServiceCreated = [bool]$State.serviceCreated
    $stateServiceConfigurationChanged = [bool]$State.serviceConfigurationChanged
    $stateDatabaseCreated = [bool]$State.databaseCreated
    $statePreviousImage = [string]$State.previousImagePath
    $stateServiceWasRunning = [bool]$State.serviceWasRunning
    $stateVersion = [string]$State.version
    if ($stateVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'The installer recovery version is invalid.' }
    $stateTargetRelease = [System.IO.Path]::GetFullPath((Join-Path $releaseRoot $stateVersion))
    $releasePrefix = ([System.IO.Path]::GetFullPath($releaseRoot)).TrimEnd('\') + '\'
    if (-not $stateTargetRelease.StartsWith($releasePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'The installer recovery target is outside the immutable release root.'
    }
    if ($stateServiceCreated) {
        $staleService = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
        if ($null -ne $staleService) {
            $expectedCreatedExecutable = Join-Path $stateTargetRelease 'TreadmillRunner.Gateway.exe'
            $actualCreatedExecutable = Get-InstallerServiceExecutablePath -ImagePath ([string]$staleService.PathName)
            if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualCreatedExecutable, $expectedCreatedExecutable)) {
                throw 'The stale installer does not own the existing gateway service image.'
            }
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
            & sc.exe delete $serviceName | Out-Null
            if ($LASTEXITCODE -ne 0 -and $null -ne (Get-ExistingGatewayService)) {
                throw 'The stale installer service could not be removed.'
            }
            if ($null -ne (Get-ExistingGatewayService)) {
                throw 'The stale installer service still exists after rollback.'
            }
        }
    }
    elseif ($stateServiceConfigurationChanged -and -not [string]::IsNullOrWhiteSpace($statePreviousImage)) {
        Restore-InstallerServiceImageSafely -PreviousImagePath $statePreviousImage `
            -TargetImagePath ([System.IO.Path]::Combine($stateTargetRelease, 'TreadmillRunner.Gateway.exe'))
    }
    Restore-InstallerScheduledTask -Name $taskName `
        -RegistrationCompleted ([bool]$State.updateTaskRegistrationCompleted) `
        -ExistedBefore ([bool]$State.updateTaskExistedBefore) `
        -PreviousXml ([string]$State.updateTaskPreviousXml) `
        -ExpectedExecute ([string]$State.updateTaskRegisteredExecute) `
        -ExpectedArguments ([string]$State.updateTaskRegisteredArguments) `
        -ExpectedUserId ([string]$State.updateTaskRegisteredUserId) `
        -ExpectedLogonType ([string]$State.updateTaskRegisteredLogonType) `
        -ExpectedRunLevel ([string]$State.updateTaskRegisteredRunLevel)
    Restore-InstallerScheduledTask -Name $guardianTaskName `
        -RegistrationCompleted ([bool]$State.guardianTaskRegistrationCompleted) `
        -ExistedBefore ([bool]$State.guardianTaskExistedBefore) `
        -PreviousXml ([string]$State.guardianTaskPreviousXml) `
        -ExpectedExecute ([string]$State.guardianTaskRegisteredExecute) `
        -ExpectedArguments ([string]$State.guardianTaskRegisteredArguments) `
        -ExpectedUserId ([string]$State.guardianTaskRegisteredUserId) `
        -ExpectedLogonType ([string]$State.guardianTaskRegisteredLogonType) `
        -ExpectedRunLevel ([string]$State.guardianTaskRegisteredRunLevel)
    Restore-InstallerProtectedFiles -State $State
    Restore-InstallerMutableInfrastructure -State $State
    if ($stateTargetOwned -and (Test-Path -LiteralPath $stateTargetRelease)) { Remove-Item -LiteralPath $stateTargetRelease -Recurse -Force -ErrorAction SilentlyContinue }
    if ($stateDatabaseCreated) {
        foreach ($sidecar in @($databasePath, "$databasePath-wal", "$databasePath-shm")) {
            if (Test-Path -LiteralPath $sidecar) { Remove-Item -LiteralPath $sidecar -Force -ErrorAction SilentlyContinue }
        }
    }
    if ([bool]$State.migrationBackupCreated) {
        $stateMigrationBackup = [string]$State.migrationBackupPath
        $resolvedMigrationBackup = [System.IO.Path]::GetFullPath($stateMigrationBackup)
        if ($resolvedMigrationBackup -notlike (([System.IO.Path]::GetFullPath($backupRoot)).TrimEnd('\') + '\.installer-' + [string]$State.transactionId + '-migration.db')) {
            throw 'The installer migration backup is outside the backup root.'
        }
        if ([string]::IsNullOrWhiteSpace([string]$State.migrationBackupSha256)) {
            throw 'The installer migration backup has no authoritative hash.'
        }
        if (Test-Path -LiteralPath $resolvedMigrationBackup -PathType Leaf) {
            if ((Get-Sha256Hex -Path $resolvedMigrationBackup) -ne [string]$State.migrationBackupSha256) {
                throw 'The installer migration backup failed hash verification.'
            }
            Remove-Item -LiteralPath "$databasePath-wal" -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath "$databasePath-shm" -Force -ErrorAction SilentlyContinue
            Copy-DurableFile -Source $resolvedMigrationBackup -Destination $databasePath
            if ((Get-Sha256Hex -Path $databasePath) -ne [string]$State.migrationBackupSha256) { throw 'The installer migration restore failed hash verification.' }
            Remove-Item -LiteralPath $resolvedMigrationBackup -Force -ErrorAction SilentlyContinue
        }
        else {
            # A prior recovery may have copied and verified the snapshot before
            # its final cleanup was interrupted. Accept that state only when
            # the durable migration phase and database hash prove the restore
            # already completed; otherwise fail closed with the evidence intact.
            $restoredMigrationPhases = @('MigrationStarted', 'MigrationCommitted', 'ServiceMutationStarted',
                'ServiceEnvironmentMutationStarted', 'DirectoryAclMutationStarted', 'UpdateTaskRegistrationStarted',
                'UpdateTaskRegistrationCompleted', 'GuardianTaskRegistrationStarted', 'GuardianTaskRegistrationCompleted')
            if ([string]$State.phase -notin $restoredMigrationPhases -or
                -not (Test-Path -LiteralPath $databasePath -PathType Leaf) -or
                (Get-Sha256Hex -Path $databasePath) -ne [string]$State.migrationBackupSha256) {
                throw 'The installer migration backup is missing and the database restore is not durably verified.'
            }
            Remove-Item -LiteralPath "$databasePath-wal" -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath "$databasePath-shm" -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $stateServiceCreated -and $stateServiceWasRunning) {
        Start-Service -Name $serviceName -ErrorAction Stop
        if ((Get-Service -Name $serviceName -ErrorAction Stop).Status -ne 'Running') {
            throw 'The restored gateway service did not return to Running.'
        }
    }
    Remove-InstallerTransactionWorkspace -TransactionId ([string]$State.transactionId)
}

function Recover-StaleInstallerTransaction {
    if (-not (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf)) { return }
    $marker = Get-Content -LiteralPath $maintenanceMarkerPath -Raw -ErrorAction Stop
    if ($marker -notmatch '^installer (?<version>\d+\.\d+\.\d+) (?<transaction>[0-9a-f]{32}) (?<pid>\d+) ') {
        throw 'The installer maintenance marker is not a recoverable transaction owned by this installer.'
    }
    $markerTransactionId = $Matches.transaction
    $markerVersion = $Matches.version
    $markerPid = [int]$Matches.pid
    $markerStatePath = Join-Path $planRoot ".installer-$markerTransactionId.json"
    if (-not (Test-Path -LiteralPath $markerStatePath -PathType Leaf)) {
        throw 'The installer maintenance marker has no recoverable transaction state.'
    }
    $state = Get-Content -LiteralPath $markerStatePath -Raw | ConvertFrom-Json
    if ([int]$state.schemaVersion -ne 1 -or [string]$state.transactionId -ne $markerTransactionId -or [int]$state.processId -ne $markerPid) {
        throw 'The installer transaction state does not match its maintenance marker.'
    }
    if ([string]$state.version -ne $markerVersion) {
        throw 'The installer transaction version does not match its maintenance marker.'
    }
    if ([string]$state.installRoot -ne $resolvedInstallRoot -or [string]$state.dataRoot -ne $resolvedDataRoot) {
        throw 'The installer transaction roots do not match this installation.'
    }
    if ([string]::IsNullOrWhiteSpace([string]$state.processStartTimeUtc) -or
        [string]::IsNullOrWhiteSpace([string]$state.processPath)) {
        throw 'The installer transaction has no process identity for safe recovery.'
    }
    $owner = Get-Process -Id $markerPid -ErrorAction SilentlyContinue
    if ($null -ne $owner) {
        try {
            $ownerStartTimeUtc = $owner.StartTime.ToUniversalTime()
            $stateStartTimeUtc = [DateTimeOffset]::Parse([string]$state.processStartTimeUtc).ToUniversalTime()
            $ownerPath = [string]$owner.Path
            if ($ownerStartTimeUtc -eq $stateStartTimeUtc -and $ownerPath -eq [string]$state.processPath) {
                throw 'The installer maintenance marker is owned by an active process.'
            }
        }
        catch {
            if ($_.Exception.Message -eq 'The installer maintenance marker is owned by an active process.') { throw }
            throw 'The installer maintenance marker owner could not be validated safely.'
        }
    }
    if ([string]$state.phase -eq 'Committed') {
        # A crash after the durable Committed record but before marker cleanup
        # must never roll back a ready installation. Clear only the stale
        # transaction evidence, even when the next requested version differs.
        Remove-Item -LiteralPath $maintenanceMarkerPath -Force -ErrorAction Stop
        Remove-Item -LiteralPath $markerStatePath -Force -ErrorAction SilentlyContinue
        Remove-InstallerTransactionWorkspace -TransactionId $markerTransactionId
        $committedMigrationWorkspace = Join-Path $backupRoot ".installer-$markerTransactionId-migration.db"
        Remove-Item -LiteralPath $committedMigrationWorkspace -Force -ErrorAction SilentlyContinue
        return
    }
    Remove-InstallerRecoveryState -State $state
    # Recovery mutations are complete before ownership evidence is removed.
    # Delete the marker first so a crash cannot leave a marker with no state.
    Remove-Item -LiteralPath $maintenanceMarkerPath -Force -ErrorAction Stop
    Remove-Item -LiteralPath $markerStatePath -Force -ErrorAction SilentlyContinue
}

$pendingPlanPath = Join-Path $planRoot 'pending-activation.json'

if (-not $PSCmdlet.ShouldProcess($serviceName, $(if ($RepairUpdateInfrastructureOnly) { 'Repair protected update infrastructure' } else { "Install release $Version and configure the Windows Service" }))) { return }
foreach ($directory in @($releaseRoot, $updaterRoot, (Split-Path -Parent $databasePath), $dataProtectionKeyPath, $backupRoot, $feedRoot, $stagingRoot, $planRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
try {
$maintenanceMutex = [System.Threading.Mutex]::new($false, 'Global\TreadmillRunnerGateway.Maintenance')
$maintenanceMutexHeld = $false
$maintenanceMutexAbandoned = $false
$maintenanceMarkerCreated = $false
try { $maintenanceMutexAcquired = $maintenanceMutex.WaitOne(30000) }
catch [System.Threading.AbandonedMutexException] { $maintenanceMutexAcquired = $true; $maintenanceMutexAbandoned = $true }
if (-not $maintenanceMutexAcquired) { throw 'The update maintenance lock could not be acquired.' }
$maintenanceMutexHeld = $true
if (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf) {
    Recover-StaleInstallerTransaction
}
if ((Test-Path -LiteralPath $pendingPlanPath -PathType Leaf) -or
    @(Get-ChildItem -LiteralPath $updaterRoot -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '.update-*' -or $_.Name -like '.service-guardian-*' }).Count -gt 0) {
    throw 'The installer cannot run while a pending activation or refresh workspace exists.'
}
$maintenanceMarkerCreated = $false
$maintenanceMarkerPathExists = Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf
if ($maintenanceMarkerPathExists) { throw 'The installer maintenance marker could not be reconciled.' }
Initialize-InstallerProtectedFileState
Write-InstallerState -Phase 'Prepared'
$maintenanceMutexHeld = $true
$markerStream = [System.IO.File]::Open($maintenanceMarkerPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
    $markerBytes = [System.Text.UTF8Encoding]::new($false).GetBytes("installer $Version $installerTransactionId $PID $([DateTimeOffset]::UtcNow.ToString('O'))")
    $markerStream.Write($markerBytes, 0, $markerBytes.Length)
    $markerStream.Flush($true)
    $maintenanceMarkerCreated = $true
}
finally { $markerStream.Dispose() }
if (Test-Path -LiteralPath $targetRelease) {
    if (-not $RepairUpdateInfrastructureOnly) { throw 'The immutable target release already exists.' }
} else {
    if ($RepairUpdateInfrastructureOnly) { throw 'Repair mode requires the selected installed release to exist.' }
    # The release directory is transaction-owned until service installation
    # starts. If extraction/copy or migration fails, the finally block can
    # remove a partial target so retrying the same version remains safe.
    $targetReleaseOwned = $true
    Write-InstallerState -Phase 'TargetOwned'
    Copy-Item -LiteralPath $resolvedRelease -Destination $targetRelease -Recurse
}
Replace-InstallerProtectedFile -Key helper -SourcePath (Join-Path $resolvedRelease 'Updates\update-helper.ps1')
Replace-InstallerProtectedFile -Key guardian -SourcePath (Join-Path $resolvedRelease 'Updates\service-guardian.ps1')
Replace-InstallerProtectedFile -Key certificate -SourcePath $resolvedCertificate

if (-not (Test-Path -LiteralPath $databasePath)) {
    if (-not [string]::IsNullOrWhiteSpace($SourceDatabasePath)) {
        $resolvedSourceDatabase = [System.IO.Path]::GetFullPath($SourceDatabasePath)
        if (-not (Test-Path -LiteralPath $resolvedSourceDatabase -PathType Leaf)) { throw 'SourceDatabasePath is missing.' }
        $databaseCreatedByTransaction = $true
        Write-InstallerState -Phase 'DatabaseOwned'
        Copy-DurableFile -Source $resolvedSourceDatabase -Destination $databasePath
    }
}
if (-not $RepairUpdateInfrastructureOnly) {
    if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf) -and -not $databaseCreatedByTransaction) {
        # A fresh migration may create the database even when no source
        # snapshot was supplied; record that ownership before starting it.
        $databaseCreatedByTransaction = $true
        Write-InstallerState -Phase 'DatabaseMigrationOwned'
    }
    $migrationBackupPath = Join-Path $backupRoot ".installer-$installerTransactionId-migration.db"
    $migrationBackupCreated = Test-Path -LiteralPath $databasePath -PathType Leaf
    if ($migrationBackupCreated) {
        Copy-DurableFile -Source $databasePath -Destination $migrationBackupPath
        $migrationBackupSha256 = Get-Sha256Hex -Path $migrationBackupPath
        Write-InstallerState -Phase 'MigrationStarted'
    }
    try {
        & (Join-Path $targetRelease 'TreadmillRunner.Migrations.exe') --connection "Data Source=$databasePath"
        if ($LASTEXITCODE -ne 0) { throw 'Initial database migration failed.' }
        if ($migrationBackupCreated) {
            # Keep the snapshot until the entire installer commits. A later
            # service/task failure must be able to restore this database too.
            Write-InstallerState -Phase 'MigrationCommitted'
        }
    }
    catch {
        Remove-Item -LiteralPath "$databasePath-wal" -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath "$databasePath-shm" -Force -ErrorAction SilentlyContinue
        if ($migrationBackupCreated) { Copy-DurableFile -Source $migrationBackupPath -Destination $databasePath }
        if ($migrationBackupCreated) { Remove-Item -LiteralPath $migrationBackupPath -Force -ErrorAction SilentlyContinue }
        $migrationBackupCreated = $false
        $migrationBackupPath = $null
        $migrationBackupSha256 = $null
        try { Write-InstallerState -Phase 'MigrationRolledBack' } catch { }
        throw
    }
}

$existing = Get-ExistingGatewayService
$serviceInstallStarted = $true
$serviceWasRunning = $null -ne $existing -and [string]$existing.Status -eq 'Running'
if ($null -eq $existing) {
    if ($RepairUpdateInfrastructureOnly) { throw 'Repair mode requires the gateway service to be installed.' }
    $serviceCreatedByTransaction = $true
    Write-InstallerState -Phase 'ServiceCreationStarted'
    & sc.exe create $serviceName 'start=' delayed-auto 'obj=' "NT SERVICE\$serviceName" 'binPath=' ('"{0}"' -f $executableTarget) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Windows Service creation failed.' }
    & sc.exe description $serviceName 'TreadmillRunner local BLE gateway and touch dashboard' | Out-Null
    & sc.exe sidtype $serviceName unrestricted | Out-Null
}
else {
    $previousService = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    $previousImagePath = [string]$previousService.PathName
    if (-not $RepairUpdateInfrastructureOnly) {
        # Record the recovery obligation before stopping the existing service.
        # If the installer process dies after this durable write, stale recovery
        # restores the prior image and running state even when sc.exe config was
        # never reached.
        $serviceConfigurationChanged = $true
        Write-InstallerState -Phase 'ServiceMutationStarted'
        Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
        & sc.exe config $serviceName 'binPath=' ('"{0}"' -f $executableTarget) 'start=' delayed-auto | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Windows Service configuration failed.' }
    }
}
$serviceRegistry = "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName"
$preservedTransportEnvironment = @()
$serviceRegistryValues = Get-ItemProperty -Path $serviceRegistry -ErrorAction Stop
$serviceEnvironmentExistedBefore = $serviceRegistryValues.PSObject.Properties.Name -contains 'Environment'
$previousServiceEnvironment = if ($serviceEnvironmentExistedBefore) { @($serviceRegistryValues.Environment) } else { @() }
$serviceEnvironmentCaptured = $true
Write-InstallerState -Phase 'ServiceEnvironmentMutationStarted'
$existingServiceEnvironment = @($previousServiceEnvironment)
if ($null -ne $existingServiceEnvironment) {
    $preservedTransportEnvironment = @($existingServiceEnvironment | Where-Object {
        $_ -match '^(?:Gateway__PublicUrl|Gateway__AllowedPublicHostSuffixes__\d+|Kestrel__Endpoints__Http__Url|Kestrel__Endpoints__Http__Protocols|Kestrel__Endpoints__Https__Url|Kestrel__Endpoints__Https__Protocols|Kestrel__Endpoints__Https__Certificate__Path|Kestrel__Endpoints__Https__Certificate__Password|Kestrel__Certificates__Default__Path|Kestrel__Certificates__Default__Password)='
    })
}
$serviceEnvironment = @(
    'ASPNETCORE_ENVIRONMENT=Production',
    'Gateway__Urls=http://0.0.0.0:5180',
    "Persistence__DatabasePath=$databasePath",
    "Persistence__DataProtectionKeyPath=$dataProtectionKeyPath",
    "Updates__InstallRoot=$resolvedInstallRoot",
    "Updates__DataRoot=$resolvedDataRoot",
    "Updates__BackupRoot=$backupRoot",
    "GarminActivityUpload__BackupRoot=$(Join-Path $backupRoot 'garmin-source')",
    "Updates__PlanRoot=$planRoot",
    "Updates__FeedPath=$feedRoot",
    'Updates__FeedProvider=GitHubThenLocal',
    'Updates__GitHubOwner=belgian-coder',
    'Updates__GitHubRepository=treadmill-runner',
    "Updates__StagingRoot=$stagingRoot",
    "Updates__SigningCertificatePath=$certificateTarget",
    'Updates__Channel=stable',
    'Updates__ServiceName=TreadmillRunnerGateway',
    'Updates__ScheduledTaskName=TreadmillRunnerUpdate',
    'Updates__HealthUrl=http://127.0.0.1:5180/health/ready'
)
$serviceEnvironment += $preservedTransportEnvironment
New-ItemProperty -Path $serviceRegistry -Name Environment -PropertyType MultiString -Value $serviceEnvironment -Force | Out-Null

$updatesRoot = Join-Path $resolvedDataRoot 'updates'
$aclPaths = @(
    $resolvedDataRoot, $resolvedInstallRoot, $updatesRoot, $feedRoot,
    (Split-Path -Parent $databasePath), $dataProtectionKeyPath, $backupRoot, $stagingRoot, $planRoot
) | Select-Object -Unique
$directoryAclStates = @($aclPaths | ForEach-Object {
    $capturedAcl = Get-Acl -LiteralPath $_ -ErrorAction Stop
    [ordered]@{
        path = [System.IO.Path]::GetFullPath($_)
        sddl = $capturedAcl.GetSecurityDescriptorSddlForm([System.Security.AccessControl.AccessControlSections]::All)
    }
})
Write-InstallerState -Phase 'DirectoryAclMutationStarted'
& icacls.exe $resolvedDataRoot /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'ProgramData ACL configuration failed.' }
& icacls.exe $resolvedInstallRoot /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' "NT SERVICE\${serviceName}:(OI)(CI)RX" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Program Files ACL configuration failed.' }
foreach ($readOnlyDirectory in @($resolvedDataRoot, $updatesRoot, $feedRoot)) {
    & icacls.exe $readOnlyDirectory /grant:r "NT SERVICE\${serviceName}:(OI)(CI)RX" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Read-only service ACL configuration failed for $readOnlyDirectory." }
}
foreach ($writableDirectory in @((Split-Path -Parent $databasePath), $dataProtectionKeyPath, $backupRoot, $stagingRoot, $planRoot)) {
    & icacls.exe $writableDirectory /grant:r "NT SERVICE\${serviceName}:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Writable service ACL configuration failed for $writableDirectory." }
}

$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -PlanPath "{1}" -InstallRoot "{2}" -DataRoot "{3}" -HealthUrl "{4}"' -f
        $helperTarget, (Join-Path $planRoot 'pending-activation.json'), $resolvedInstallRoot, $resolvedDataRoot,
        'http://127.0.0.1:5180/health/ready')
$taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$updateTaskRegisteredExecute = [string]$taskAction.Execute
$updateTaskRegisteredArguments = [string]$taskAction.Arguments
$updateTaskRegisteredUserId = [string]$taskPrincipal.UserId
$updateTaskRegisteredLogonType = [string]$taskPrincipal.LogonType
$updateTaskRegisteredRunLevel = [string]$taskPrincipal.RunLevel
$taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
$existingUpdateTask = Get-ExistingRootScheduledTask -Name $taskName
if ($null -ne $existingUpdateTask) {
    $updateTaskExistedBefore = $true
    $updateTaskPreviousXml = (Export-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop | Out-String).Trim()
}
$updateTaskCreatedByTransaction = $true
Write-InstallerState -Phase 'UpdateTaskRegistrationStarted'
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
$serviceSid = ([System.Security.Principal.NTAccount]::new("NT SERVICE\$serviceName")).Translate(
    [System.Security.Principal.SecurityIdentifier]).Value
$taskService = New-Object -ComObject 'Schedule.Service'
$taskService.Connect()
$registeredTask = $taskService.GetFolder('\').GetTask($taskName)
$registeredTask.SetSecurityDescriptor(
    "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;$serviceSid)",
    0)
$updateTaskRegisteredExecute = [string]$taskAction.Execute
$updateTaskRegisteredArguments = [string]$taskAction.Arguments
$updateTaskRegisteredUserId = [string]$taskPrincipal.UserId
$updateTaskRegisteredLogonType = [string]$taskPrincipal.LogonType
$updateTaskRegisteredRunLevel = [string]$taskPrincipal.RunLevel
Assert-InstallerTaskMatchesRegistration -Name $taskName -ExpectedExecute $updateTaskRegisteredExecute `
    -ExpectedArguments $updateTaskRegisteredArguments -ExpectedUserId $updateTaskRegisteredUserId `
    -ExpectedLogonType $updateTaskRegisteredLogonType -ExpectedRunLevel $updateTaskRegisteredRunLevel
$updateTaskRegistrationCompleted = $true
Write-InstallerState -Phase 'UpdateTaskRegistrationCompleted'

$guardianAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -DataRoot "{1}" -HealthUrl "{2}"' -f
        $guardianTarget, $resolvedDataRoot, 'http://127.0.0.1:5180/health/live')
$guardianTriggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$guardianSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable
$guardianTaskRegisteredExecute = [string]$guardianAction.Execute
$guardianTaskRegisteredArguments = [string]$guardianAction.Arguments
$guardianTaskRegisteredUserId = [string]$taskPrincipal.UserId
$guardianTaskRegisteredLogonType = [string]$taskPrincipal.LogonType
$guardianTaskRegisteredRunLevel = [string]$taskPrincipal.RunLevel
$existingGuardianTask = Get-ExistingRootScheduledTask -Name $guardianTaskName
if ($null -ne $existingGuardianTask) {
    $guardianTaskExistedBefore = $true
    $guardianTaskPreviousXml = (Export-ScheduledTask -TaskName $guardianTaskName -TaskPath '\' -ErrorAction Stop | Out-String).Trim()
}
$guardianTaskCreatedByTransaction = $true
Write-InstallerState -Phase 'GuardianTaskRegistrationStarted'
Register-ScheduledTask -TaskName $guardianTaskName -Action $guardianAction -Trigger $guardianTriggers `
    -Principal $taskPrincipal -Settings $guardianSettings -Force | Out-Null
$guardianTaskRegisteredExecute = [string]$guardianAction.Execute
$guardianTaskRegisteredArguments = [string]$guardianAction.Arguments
$guardianTaskRegisteredUserId = [string]$taskPrincipal.UserId
$guardianTaskRegisteredLogonType = [string]$taskPrincipal.LogonType
$guardianTaskRegisteredRunLevel = [string]$taskPrincipal.RunLevel
Assert-InstallerTaskMatchesRegistration -Name $guardianTaskName -ExpectedExecute $guardianTaskRegisteredExecute `
    -ExpectedArguments $guardianTaskRegisteredArguments -ExpectedUserId $guardianTaskRegisteredUserId `
    -ExpectedLogonType $guardianTaskRegisteredLogonType -ExpectedRunLevel $guardianTaskRegisteredRunLevel
$guardianTaskRegistrationCompleted = $true
Write-InstallerState -Phase 'GuardianTaskRegistrationCompleted'

if ($RepairUpdateInfrastructureOnly) {
    if ($serviceWasRunning) { Start-Service -Name $serviceName -ErrorAction Stop }
    Write-InstallerState -Phase 'Committed'
    $installationCommitted = $true
    Commit-InstallerMigrationBackup
    Set-PostCommitOperationalInfrastructure
    Write-Host "Protected update infrastructure repaired for the running $serviceName service."
    return
}
Start-Service -Name $serviceName
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
do {
    try {
        $ready = Invoke-WebRequest -Uri 'http://127.0.0.1:5180/health/ready' -UseBasicParsing -TimeoutSec 3
        if ($ready.StatusCode -eq 200) {
            Write-InstallerState -Phase 'Committed'
            $installationCommitted = $true
            Commit-InstallerMigrationBackup
            Set-PostCommitOperationalInfrastructure
            Write-Host "TreadmillRunnerGateway $Version is ready at http://127.0.0.1:5180"
            return
        }
    }
    catch { }
    Start-Sleep -Seconds 2
} while ([DateTimeOffset]::UtcNow -lt $deadline)
throw 'The installed gateway did not become ready within 120 seconds.'
}
finally {
    $cleanupSucceeded = $true
    if (-not $installationCommitted) {
        try {
            if ($serviceCreatedByTransaction) {
                $createdService = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
                if ($null -ne $createdService) {
                    $actualCreatedExecutable = Get-InstallerServiceExecutablePath -ImagePath ([string]$createdService.PathName)
                    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualCreatedExecutable, $executableTarget)) {
                        throw 'The installer does not own the existing gateway service image.'
                    }
                    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
                    & sc.exe delete $serviceName | Out-Null
                    if ($LASTEXITCODE -ne 0 -and $null -ne (Get-ExistingGatewayService)) {
                        throw 'The partially created gateway service could not be removed.'
                    }
                }
            }
            elseif ($serviceConfigurationChanged -and -not [string]::IsNullOrWhiteSpace($previousImagePath)) {
                Restore-InstallerServiceImageSafely -PreviousImagePath $previousImagePath -TargetImagePath $executableTarget
            }
            Restore-InstallerScheduledTask -Name $taskName `
                -RegistrationCompleted $updateTaskRegistrationCompleted `
                -ExistedBefore $updateTaskExistedBefore `
                -PreviousXml $updateTaskPreviousXml `
                -ExpectedExecute $updateTaskRegisteredExecute `
                -ExpectedArguments $updateTaskRegisteredArguments `
                -ExpectedUserId $updateTaskRegisteredUserId `
                -ExpectedLogonType $updateTaskRegisteredLogonType `
                -ExpectedRunLevel $updateTaskRegisteredRunLevel
            Restore-InstallerScheduledTask -Name $guardianTaskName `
                -RegistrationCompleted $guardianTaskRegistrationCompleted `
                -ExistedBefore $guardianTaskExistedBefore `
                -PreviousXml $guardianTaskPreviousXml `
                -ExpectedExecute $guardianTaskRegisteredExecute `
                -ExpectedArguments $guardianTaskRegisteredArguments `
                -ExpectedUserId $guardianTaskRegisteredUserId `
                -ExpectedLogonType $guardianTaskRegisteredLogonType `
                -ExpectedRunLevel $guardianTaskRegisteredRunLevel
            if ($targetReleaseOwned -and (Test-Path -LiteralPath $targetRelease)) {
                Remove-Item -LiteralPath $targetRelease -Recurse -Force -ErrorAction Stop
                if (Test-Path -LiteralPath $targetRelease) { throw 'The partial target release could not be removed.' }
            }
            if ($databaseCreatedByTransaction) {
                foreach ($sidecar in @($databasePath, "$databasePath-wal", "$databasePath-shm")) {
                    if (Test-Path -LiteralPath $sidecar) { Remove-Item -LiteralPath $sidecar -Force -ErrorAction Stop }
                }
            }
            if ($migrationBackupCreated) {
                Remove-Item -LiteralPath "$databasePath-wal" -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath "$databasePath-shm" -Force -ErrorAction SilentlyContinue
                Copy-DurableFile -Source $migrationBackupPath -Destination $databasePath
            }
            if (-not (Test-Path -LiteralPath $installerStatePath -PathType Leaf)) {
                throw 'The installer rollback state is missing.'
            }
            $rollbackState = Get-Content -LiteralPath $installerStatePath -Raw -ErrorAction Stop | ConvertFrom-Json
            if ([string]$rollbackState.transactionId -ne $installerTransactionId) {
                throw 'The installer rollback state does not belong to this transaction.'
            }
            Restore-InstallerProtectedFiles -State $rollbackState
            Restore-InstallerMutableInfrastructure -State $rollbackState
            if (-not $serviceCreatedByTransaction -and $serviceWasRunning) {
                Start-Service -Name $serviceName -ErrorAction Stop
                if ((Get-Service -Name $serviceName -ErrorAction Stop).Status -ne 'Running') {
                    throw 'The restored gateway service did not return to Running.'
                }
            }
        }
        catch {
            $cleanupSucceeded = $false
        }
    }
    if ($installationCommitted -or $cleanupSucceeded) {
        Remove-InstallerStateAndMarker
    }
    if ($maintenanceMutexHeld) {
        $maintenanceMutex.ReleaseMutex()
        $maintenanceMutex.Dispose()
    }
}
