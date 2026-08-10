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
    $runningTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $runningTask -and $runningTask.State -eq 'Running') {
        throw 'The update task is running; protected update infrastructure cannot be repaired now.'
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

if (-not $PSCmdlet.ShouldProcess($serviceName, $(if ($RepairUpdateInfrastructureOnly) { 'Repair protected update infrastructure' } else { "Install release $Version and configure the Windows Service" }))) { return }
foreach ($directory in @($releaseRoot, $updaterRoot, (Split-Path -Parent $databasePath), $dataProtectionKeyPath, $backupRoot, $feedRoot, $stagingRoot, $planRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
[System.IO.File]::WriteAllText(
    $maintenanceMarkerPath,
    "installer $Version $([DateTimeOffset]::UtcNow.ToString('O'))",
    [System.Text.UTF8Encoding]::new($false))
try {
if (Test-Path -LiteralPath $targetRelease) {
    if (-not $RepairUpdateInfrastructureOnly) { throw 'The immutable target release already exists.' }
} else {
    if ($RepairUpdateInfrastructureOnly) { throw 'Repair mode requires the selected installed release to exist.' }
    Copy-Item -LiteralPath $resolvedRelease -Destination $targetRelease -Recurse
}
Copy-Item -LiteralPath (Join-Path $resolvedRelease 'Updates\update-helper.ps1') -Destination $helperTarget -Force
Copy-Item -LiteralPath (Join-Path $resolvedRelease 'Updates\service-guardian.ps1') -Destination $guardianTarget -Force
Copy-Item -LiteralPath $resolvedCertificate -Destination $certificateTarget -Force

if (-not (Test-Path -LiteralPath $databasePath)) {
    if (-not [string]::IsNullOrWhiteSpace($SourceDatabasePath)) {
        $resolvedSourceDatabase = [System.IO.Path]::GetFullPath($SourceDatabasePath)
        if (-not (Test-Path -LiteralPath $resolvedSourceDatabase -PathType Leaf)) { throw 'SourceDatabasePath is missing.' }
        Copy-Item -LiteralPath $resolvedSourceDatabase -Destination $databasePath
    }
}
if (-not $RepairUpdateInfrastructureOnly) {
    & (Join-Path $targetRelease 'TreadmillRunner.Migrations.exe') --connection "Data Source=$databasePath"
    if ($LASTEXITCODE -ne 0) { throw 'Initial database migration failed.' }
}

$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    if ($RepairUpdateInfrastructureOnly) { throw 'Repair mode requires the gateway service to be installed.' }
    & sc.exe create $serviceName 'start=' delayed-auto 'obj=' "NT SERVICE\$serviceName" 'binPath=' ('"{0}"' -f $executableTarget) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Windows Service creation failed.' }
    & sc.exe description $serviceName 'TreadmillRunner local BLE gateway and touch dashboard' | Out-Null
    & sc.exe sidtype $serviceName unrestricted | Out-Null
}
else {
    Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    if (-not $RepairUpdateInfrastructureOnly) {
        & sc.exe config $serviceName 'binPath=' ('"{0}"' -f $executableTarget) 'start=' delayed-auto | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Windows Service configuration failed.' }
    }
}
& sc.exe failure $serviceName 'reset=' 86400 'actions=' restart/5000/restart/15000/restart/60000 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Windows Service recovery actions could not be configured.' }
& sc.exe failureflag $serviceName 1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Windows Service non-crash recovery could not be configured.' }

$serviceRegistry = "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName"
$serviceEnvironment = @(
    'ASPNETCORE_ENVIRONMENT=Production',
    'Gateway__Urls=http://0.0.0.0:5180',
    "Persistence__DatabasePath=$databasePath",
    "Persistence__DataProtectionKeyPath=$dataProtectionKeyPath",
    "Updates__InstallRoot=$resolvedInstallRoot",
    "Updates__DataRoot=$resolvedDataRoot",
    "Updates__BackupRoot=$backupRoot",
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
New-ItemProperty -Path $serviceRegistry -Name Environment -PropertyType MultiString -Value $serviceEnvironment -Force | Out-Null

& icacls.exe $resolvedDataRoot /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'ProgramData ACL configuration failed.' }
& icacls.exe $resolvedInstallRoot /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' "NT SERVICE\${serviceName}:(OI)(CI)RX" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Program Files ACL configuration failed.' }
$updatesRoot = Join-Path $resolvedDataRoot 'updates'
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
$taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
$serviceSid = ([System.Security.Principal.NTAccount]::new("NT SERVICE\$serviceName")).Translate(
    [System.Security.Principal.SecurityIdentifier]).Value
$taskService = New-Object -ComObject 'Schedule.Service'
$taskService.Connect()
$registeredTask = $taskService.GetFolder('\').GetTask($taskName)
$registeredTask.SetSecurityDescriptor(
    "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;$serviceSid)",
    0)

$guardianAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -DataRoot "{1}" -HealthUrl "{2}"' -f
        $guardianTarget, $resolvedDataRoot, 'http://127.0.0.1:5180/health/live')
$guardianTriggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
)
$guardianSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $guardianTaskName -Action $guardianAction -Trigger $guardianTriggers `
    -Principal $taskPrincipal -Settings $guardianSettings -Force | Out-Null

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

Start-Service -Name $serviceName
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
do {
    try {
        $ready = Invoke-WebRequest -Uri 'http://127.0.0.1:5180/health/ready' -UseBasicParsing -TimeoutSec 3
        if ($ready.StatusCode -eq 200) {
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
    Remove-Item -LiteralPath $maintenanceMarkerPath -Force -ErrorAction SilentlyContinue
}
