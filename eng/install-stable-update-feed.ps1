#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][ValidatePattern('^\d+\.\d+\.\d+$')][string] $Version,
    [Parameter(Mandatory)][string] $SourceFeed,
    [Parameter(Mandatory)][string] $PublicCertificatePath,
    [string] $InstallRoot = "$env:ProgramFiles\TreadmillRunner",
    [string] $DataRoot = "$env:ProgramData\TreadmillRunner"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath($SourceFeed)
$resolvedCertificate = [System.IO.Path]::GetFullPath($PublicCertificatePath)
$resolvedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$destinationFeed = Join-Path $resolvedDataRoot 'updates\feed'
$maintenanceMarkerPath = Join-Path $resolvedDataRoot 'updates\service-maintenance.lock'
$pendingPlanPath = Join-Path $resolvedDataRoot 'updates\plans\pending-activation.json'
$pinnedCertificate = Join-Path $resolvedInstallRoot 'updater\signing.cer'
$manifestSource = Join-Path $sourceRoot 'stable.manifest.json'

if (-not (Test-Path -LiteralPath $manifestSource -PathType Leaf)) { throw 'The stable manifest is missing.' }
if (-not (Test-Path -LiteralPath $resolvedCertificate -PathType Leaf)) { throw 'The public signing certificate is missing.' }
if (-not (Test-Path -LiteralPath $pinnedCertificate -PathType Leaf)) {
    throw 'The administrator-pinned updater certificate is missing. Run install-gateway-service.ps1 first.'
}
$suppliedThumbprint = ([System.Security.Cryptography.X509Certificates.X509Certificate2]::new($resolvedCertificate)).Thumbprint
$pinnedThumbprint = ([System.Security.Cryptography.X509Certificates.X509Certificate2]::new($pinnedCertificate)).Thumbprint
if ($suppliedThumbprint -ne $pinnedThumbprint) {
    throw 'The supplied release certificate does not match the administrator-pinned updater certificate.'
}
$manifestReadStream = [System.IO.File]::Open(
    $manifestSource, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
$manifestMemory = [System.IO.MemoryStream]::new()
try { $manifestReadStream.CopyTo($manifestMemory) }
finally { $manifestReadStream.Dispose() }
$validatedManifestBytes = $manifestMemory.ToArray()
$manifestMemory.Dispose()
$manifestHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $validatedManifestSha256 = ([System.BitConverter]::ToString(
        $manifestHasher.ComputeHash($validatedManifestBytes))).Replace('-', '').ToUpperInvariant()
}
finally { $manifestHasher.Dispose() }
$manifestJson = [System.Text.UTF8Encoding]::new($false, $true).GetString($validatedManifestBytes)
$manifest = $manifestJson | ConvertFrom-Json
if ([string]$manifest.version -ne $Version -or [string]$manifest.channel -ne 'stable') {
    throw 'The stable manifest identity does not match the requested release.'
}
$packageName = [System.IO.Path]::GetFileName([string]$manifest.packageFileName)
if ($packageName -ne [string]$manifest.packageFileName -or -not $packageName.EndsWith('.zip', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The stable package name is invalid.'
}
$packageSource = Join-Path $sourceRoot $packageName
if (-not (Test-Path -LiteralPath $packageSource -PathType Leaf)) { throw 'The stable package is missing.' }
if ((Get-Item -LiteralPath $packageSource).Length -gt 1GB) { throw 'The stable package is too large.' }
$normalizedNotes = ([string]$manifest.releaseNotes).Replace("`r`n", "`n").Replace("`r", "`n")
$payload = @(
    [string]$manifest.schemaVersion,
    [string]$manifest.version,
    [string]$manifest.channel,
    [string]$manifest.packageFileName,
    ([string]$manifest.packageSha256).ToUpperInvariant(),
    [string]$manifest.minimumDatabaseSchemaVersion,
    [string]$manifest.maximumDatabaseSchemaVersion,
    $normalizedNotes
) -join "`n"
$certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($resolvedCertificate)
$rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
try {
    if ($null -eq $rsa -or -not $rsa.VerifyData(
        [System.Text.Encoding]::UTF8.GetBytes($payload),
        [Convert]::FromBase64String([string]$manifest.signature),
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)) {
        throw 'The stable manifest signature is invalid for the supplied public certificate.'
    }
}
finally {
    if ($null -ne $rsa) { $rsa.Dispose() }
    $certificate.Dispose()
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($packageSource)
try {
    if ($archive.Entries.Count -gt 10000) { throw 'The stable package contains too many entries.' }
    $entryNames = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $expandedBytes = [long]0
    foreach ($entry in $archive.Entries) {
        $entryName = $entry.FullName.Replace('\', '/')
        $segments = @($entryName.Split('/') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        if ([string]::IsNullOrWhiteSpace($entryName) -or $entryName.StartsWith('/') -or $entryName.Contains(':') -or
            ($segments | Where-Object { $_ -eq '.' -or $_ -eq '..' }) -or -not $entryNames.Add($entryName)) {
            throw 'The stable package contains an unsafe archive path.'
        }
        if ([long]$entry.Length -gt (2GB - $expandedBytes)) { throw 'The expanded stable package is too large.' }
        $expandedBytes += [long]$entry.Length
    }
    foreach ($required in @('TreadmillRunner.Gateway.exe', 'TreadmillRunner.Migrations.exe', 'Updates\update-helper.ps1', 'Updates\service-guardian.ps1')) {
        if (-not $entryNames.Contains($required.Replace('\', '/'))) { throw "The stable package is missing $required." }
    }
    # The open archive denies writers while the exact validated package hash
    # is captured. Publication later accepts only bytes matching this hash.
    $actualHash = (Get-FileHash -LiteralPath $packageSource -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actualHash -ne ([string]$manifest.packageSha256).ToUpperInvariant()) { throw 'The stable package hash does not match its manifest.' }
}
finally { $archive.Dispose() }

if (-not $PSCmdlet.ShouldProcess($destinationFeed, "Install trusted stable update $Version")) { return }
New-Item -ItemType Directory -Path $destinationFeed -Force | Out-Null
$transactionId = [Guid]::NewGuid().ToString('N')
$temporaryPackage = Join-Path $destinationFeed ".$packageName.$([Guid]::NewGuid().ToString('N')).tmp"
$temporaryManifest = Join-Path $destinationFeed ".stable.manifest.$([Guid]::NewGuid().ToString('N')).tmp"
$transactionRoot = Join-Path $destinationFeed ".feed-transaction-$transactionId"
$transactionStatePath = Join-Path $transactionRoot 'state.json'
$backupPackage = Join-Path $transactionRoot $packageName
$backupManifest = Join-Path $transactionRoot 'stable.manifest.json'
$destinationPackage = Join-Path $destinationFeed $packageName
$destinationManifest = Join-Path $destinationFeed 'stable.manifest.json'
$hadPackage = $false
$hadManifest = $false
$maintenanceMutex = [System.Threading.Mutex]::new($false, 'Global\TreadmillRunnerGateway.Maintenance')
$maintenanceMutexHeld = $false
$maintenanceMarkerCreated = $false
$publicationVerified = $false
$rollbackVerified = $false
$preTransactionCleanupRequired = $false
$transactionStarted = $false
$previousPackageSha256 = $null
$previousManifestSha256 = $null
$manifestSha256 = $null
$feedReplacementUnresolvedPaths = @()
$feedProcess = Get-Process -Id $PID -ErrorAction Stop
$feedProcessStartTimeUtc = $feedProcess.StartTime.ToUniversalTime().ToString('O')
$feedProcessPath = [string]$feedProcess.Path

function Get-FeedSha256 {
    param([Parameter(Mandatory)][string]$Path)
    return ([string](Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash).ToUpperInvariant()
}

function Get-FeedReplacementBackupPath {
    param([Parameter(Mandatory)][string]$Destination, [Parameter(Mandatory)][string]$TransactionId)
    if ($TransactionId -notmatch '^[0-9a-fA-F]{32}$') { throw 'The feed replacement transaction id is invalid.' }
    return "$Destination.replace-backup-$TransactionId"
}

function Copy-DurableFile {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination,
        [Parameter(Mandatory)][string]$TransactionId
    )
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
    if ([System.IO.File]::Exists($Destination)) {
        Replace-DurableFile -Source $temporary -Destination $Destination -TransactionId $TransactionId
    }
    else { [System.IO.File]::Move($temporary, $Destination) }
}

function Replace-DurableFile {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination,
        [Parameter(Mandatory)][string]$TransactionId
    )
    # File.Replace requires a non-empty backup path on Windows/.NET. Keep the
    # backup transaction-owned and deterministic. If Replace throws after
    # moving the old destination, reconcile it before preserving evidence.
    $replacementBackup = Get-FeedReplacementBackupPath -Destination $Destination -TransactionId $TransactionId
    $replaceCompleted = $false
    try {
        [System.IO.File]::Replace($Source, $Destination, $replacementBackup, $true)
        $replaceCompleted = $true
    }
    catch {
        $failure = $_.Exception.Message
        if ([System.IO.File]::Exists($replacementBackup)) {
            if (-not [System.IO.File]::Exists($Destination)) {
                try {
                    [System.IO.File]::Move($replacementBackup, $Destination)
                }
                catch {
                    $script:feedReplacementUnresolvedPaths += $replacementBackup
                    throw "Durable replacement failed and its backup could not restore the missing destination; the backup was preserved at $replacementBackup. Original: $failure"
                }
            }
            else {
                $script:feedReplacementUnresolvedPaths += $replacementBackup
                throw "Durable replacement failed with its replacement backup preserved at $replacementBackup. Original: $failure"
            }
        }
        throw
    }
    finally {
        if ($replaceCompleted -and [System.IO.File]::Exists($replacementBackup)) {
            try {
                [System.IO.File]::Delete($replacementBackup)
            }
            catch {
                $script:feedReplacementUnresolvedPaths += $replacementBackup
                throw "Durable replacement completed but its backup could not be removed; the backup was preserved at $replacementBackup. Original: $($_.Exception.Message)"
            }
        }
    }
}

function Remove-FeedReplacementBackup {
    param([Parameter(Mandatory)][string]$Destination, [Parameter(Mandatory)][string]$TransactionId)
    $replacementBackup = Get-FeedReplacementBackupPath -Destination $Destination -TransactionId $TransactionId
    if ([System.IO.File]::Exists($replacementBackup)) {
        [System.IO.File]::Delete($replacementBackup)
    }
}

function Write-FeedTransactionState {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$TransactionId,
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$PackageName,
        [Parameter(Mandatory)][string]$Phase,
        [bool]$SnapshotReady = $false,
        [bool]$HadPackage = $false,
        [bool]$HadManifest = $false,
        [string]$PackageSha256,
        [string]$ManifestSha256,
        [string]$PreviousPackageSha256,
        [string]$PreviousManifestSha256,
        [string]$TemporaryPackage,
        [string]$TemporaryManifest
    )
    $state = [ordered]@{
        schemaVersion = 1
        transactionId = $TransactionId
        processId = $PID
        processStartTimeUtc = $feedProcessStartTimeUtc
        processPath = $feedProcessPath
        version = $Version
        packageName = $PackageName
        phase = $Phase
        snapshotReady = $SnapshotReady
        hadPackage = $HadPackage
        hadManifest = $HadManifest
        packageSha256 = $PackageSha256
        manifestSha256 = $ManifestSha256
        previousPackageSha256 = $PreviousPackageSha256
        previousManifestSha256 = $PreviousManifestSha256
        temporaryPackage = $TemporaryPackage
        temporaryManifest = $TemporaryManifest
        updatedAtUtc = [DateTimeOffset]::UtcNow.ToString('O')
    }
    $temporary = "$Path.tmp"
    $payload = [System.Text.UTF8Encoding]::new($false).GetBytes(($state | ConvertTo-Json -Depth 10 -Compress))
    $stream = [System.IO.FileStream]::new($temporary, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None, 4096, [System.IO.FileOptions]::WriteThrough)
    try {
        $stream.Write($payload, 0, $payload.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
    if ([System.IO.File]::Exists($Path)) {
        Replace-DurableFile -Source $temporary -Destination $Path -TransactionId $TransactionId
    }
    else {
        [System.IO.File]::Move($temporary, $Path)
    }
}

function Remove-OwnedFeedMarker {
    param([Parameter(Mandatory)][string]$TransactionId)
    if (-not (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf)) { return }
    $content = Get-Content -LiteralPath $maintenanceMarkerPath -Raw -ErrorAction Stop
    if ($content -notmatch "^feed $([regex]::Escape($TransactionId)) ") {
        throw 'The stable feed maintenance marker is owned by another transaction.'
    }
    Remove-Item -LiteralPath $maintenanceMarkerPath -Force -ErrorAction Stop
}

function Assert-FeedPathUnderDestination {
    param([Parameter(Mandatory)][string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetFullPath($destinationFeed)
    $prefix = $root.TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'The feed transaction state contains a path outside the stable destination feed.'
    }
    return $resolved
}

function Recover-StaleFeedTransaction {
    if (-not (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf)) { return }
    $marker = Get-Content -LiteralPath $maintenanceMarkerPath -Raw -ErrorAction Stop
    if ($marker -notmatch '^feed (?<transaction>[0-9a-f]{32}) (?<pid>\d+) ') {
        throw 'The stable feed maintenance marker is not a recoverable transaction.'
    }
    $staleTransactionId = $Matches.transaction
    $stalePid = [int]$Matches.pid
    $staleRoot = Join-Path $destinationFeed ".feed-transaction-$staleTransactionId"
    $staleStatePath = Join-Path $staleRoot 'state.json'
    $staleStateReplacementBackup = Get-FeedReplacementBackupPath -Destination $staleStatePath -TransactionId $staleTransactionId
    Assert-FeedPathUnderDestination -Path $staleStateReplacementBackup | Out-Null
    if (-not (Test-Path -LiteralPath $staleStatePath -PathType Leaf)) {
        if (Test-Path -LiteralPath $staleStateReplacementBackup -PathType Leaf) {
            try {
                [System.IO.File]::Move($staleStateReplacementBackup, $staleStatePath)
            }
            catch {
                throw "The stable feed transaction state is missing and its deterministic replacement backup could not be restored: $($_.Exception.Message)"
            }
        }
        else {
            throw 'The stable feed maintenance marker has no recoverable transaction state.'
        }
    }
    $state = Get-Content -LiteralPath $staleStatePath -Raw | ConvertFrom-Json
    if ([int]$state.schemaVersion -ne 1 -or [string]$state.transactionId -ne $staleTransactionId) {
        throw 'The stable feed transaction state does not match its marker.'
    }
    if ([int]$state.processId -ne $stalePid -or [string]::IsNullOrWhiteSpace([string]$state.processStartTimeUtc) -or [string]::IsNullOrWhiteSpace([string]$state.processPath)) {
        throw 'The stable feed transaction process identity is invalid.'
    }
    $owner = Get-Process -Id $stalePid -ErrorAction SilentlyContinue
    if ($null -ne $owner) {
        try {
            if ($owner.StartTime.ToUniversalTime() -eq [DateTimeOffset]::Parse([string]$state.processStartTimeUtc).ToUniversalTime() -and
                [string]$owner.Path -eq [string]$state.processPath) {
                throw 'The stable feed maintenance marker is owned by an active process.'
            }
        }
        catch {
            if ($_.Exception.Message -eq 'The stable feed maintenance marker is owned by an active process.') { throw }
            throw 'The stable feed transaction owner could not be validated safely.'
        }
    }
    $stalePackageName = [System.IO.Path]::GetFileName([string]$state.packageName)
    if ($stalePackageName -ne [string]$state.packageName -or -not $stalePackageName.EndsWith('.zip', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The stable feed transaction package name is invalid.'
    }
    $stalePackage = Join-Path $destinationFeed $stalePackageName
    $staleManifest = Join-Path $destinationFeed 'stable.manifest.json'
    $staleBackupPackage = Join-Path $staleRoot $stalePackageName
    $staleBackupManifest = Join-Path $staleRoot 'stable.manifest.json'
    $staleReplacementBackupPackage = Get-FeedReplacementBackupPath -Destination $stalePackage -TransactionId $staleTransactionId
    $staleReplacementBackupManifest = Get-FeedReplacementBackupPath -Destination $staleManifest -TransactionId $staleTransactionId
    foreach ($path in @($staleStateReplacementBackup, $stalePackage, $staleManifest, $staleBackupPackage, $staleBackupManifest,
            $staleReplacementBackupPackage, $staleReplacementBackupManifest,
            [string]$state.temporaryPackage, [string]$state.temporaryManifest)) {
        if (-not [string]::IsNullOrWhiteSpace($path)) { Assert-FeedPathUnderDestination -Path $path | Out-Null }
    }
    $phase = [string]$state.phase
    if ($phase -eq 'Published') {
        # The new pair was verified before the crash; preserve it and remove
        # only the transaction-owned marker/workspace.
        if ([string]$state.packageSha256 -notmatch '^[0-9A-Fa-f]{64}$' -or [string]$state.manifestSha256 -notmatch '^[0-9A-Fa-f]{64}$' -or
            -not (Test-Path -LiteralPath $stalePackage -PathType Leaf) -or
            -not (Test-Path -LiteralPath $staleManifest -PathType Leaf) -or
            (Get-FeedSha256 -Path $stalePackage) -ne [string]$state.packageSha256 -or
            (Get-FeedSha256 -Path $staleManifest) -ne [string]$state.manifestSha256) {
            throw 'The published stable feed pair failed durable recovery verification.'
        }
        Remove-FeedReplacementBackup -Destination $stalePackage -TransactionId $staleTransactionId
        Remove-FeedReplacementBackup -Destination $staleManifest -TransactionId $staleTransactionId
        Remove-OwnedFeedMarker -TransactionId $staleTransactionId
    }
    elseif ($phase -in @('MarkerCreated', 'BackupsReady', 'ReplacementStarted')) {
        if ($phase -eq 'ReplacementStarted') {
            if (-not [bool]$state.snapshotReady) { throw 'The stable feed replacement state has no durable snapshot.' }
            if ([string]$state.packageSha256 -notmatch '^[0-9A-Fa-f]{64}$' -or [string]$state.manifestSha256 -notmatch '^[0-9A-Fa-f]{64}$') {
                throw 'The stable feed transaction state has invalid publication hashes.'
            }
            $allowedPackageHashes = @([string]$state.packageSha256, [string]$state.previousPackageSha256) | Where-Object { $_ -match '^[0-9A-Fa-f]{64}$' }
            $allowedManifestHashes = @([string]$state.manifestSha256, [string]$state.previousManifestSha256) | Where-Object { $_ -match '^[0-9A-Fa-f]{64}$' }
            $hashChecks = @(
                [pscustomobject]@{ Path = $stalePackage; AllowedHashes = @($allowedPackageHashes) }
                [pscustomobject]@{ Path = $staleManifest; AllowedHashes = @($allowedManifestHashes) }
            )
            foreach ($check in $hashChecks) {
                $path = [string]$check.Path
                if (Test-Path -LiteralPath $path -PathType Leaf) {
                    if ((Get-FeedSha256 -Path $path) -notin @($check.AllowedHashes)) {
                        throw 'The stable feed destination changed outside the recorded transaction.'
                    }
                }
            }
            if ([bool]$state.hadPackage) {
                if (-not (Test-Path -LiteralPath $staleBackupPackage -PathType Leaf) -or (Get-FeedSha256 -Path $staleBackupPackage) -ne [string]$state.previousPackageSha256) { throw 'The previous stable package backup is missing or corrupt.' }
                if (Test-Path -LiteralPath $stalePackage -PathType Leaf) {
                    Remove-FeedReplacementBackup -Destination $stalePackage -TransactionId $staleTransactionId
                }
                Copy-DurableFile -Source $staleBackupPackage -Destination $stalePackage -TransactionId $staleTransactionId
            }
            elseif (Test-Path -LiteralPath $stalePackage -PathType Leaf) { Remove-Item -LiteralPath $stalePackage -Force }
            if ([bool]$state.hadManifest) {
                if (-not (Test-Path -LiteralPath $staleBackupManifest -PathType Leaf) -or (Get-FeedSha256 -Path $staleBackupManifest) -ne [string]$state.previousManifestSha256) { throw 'The previous stable manifest backup is missing or corrupt.' }
                if (Test-Path -LiteralPath $staleManifest -PathType Leaf) {
                    Remove-FeedReplacementBackup -Destination $staleManifest -TransactionId $staleTransactionId
                }
                Copy-DurableFile -Source $staleBackupManifest -Destination $staleManifest -TransactionId $staleTransactionId
            }
            elseif (Test-Path -LiteralPath $staleManifest -PathType Leaf) { Remove-Item -LiteralPath $staleManifest -Force }
            if ([bool]$state.hadPackage -and (Get-FeedSha256 -Path $stalePackage) -ne [string]$state.previousPackageSha256) { throw 'The previous stable package could not be restored.' }
            if ([bool]$state.hadManifest -and (Get-FeedSha256 -Path $staleManifest) -ne [string]$state.previousManifestSha256) { throw 'The previous stable manifest could not be restored.' }
            Remove-FeedReplacementBackup -Destination $stalePackage -TransactionId $staleTransactionId
            Remove-FeedReplacementBackup -Destination $staleManifest -TransactionId $staleTransactionId
        }
        Remove-OwnedFeedMarker -TransactionId $staleTransactionId
    }
    else {
        throw "The stable feed transaction has an unknown phase: $phase."
    }
    if (Test-Path -LiteralPath $staleRoot) { Remove-Item -LiteralPath $staleRoot -Recurse -Force -ErrorAction Stop }
}

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

try {
    if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) {
        throw 'The update maintenance lock could not be acquired.'
    }
    $maintenanceMutexHeld = $true
    if (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf) {
        Recover-StaleFeedTransaction
    }
    if ((Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf) -or
        (Test-Path -LiteralPath $pendingPlanPath -PathType Leaf)) {
        throw 'The stable feed cannot be published while service maintenance or activation is in progress.'
    }
    New-Item -ItemType Directory -Path $transactionRoot -Force | Out-Null
    Write-FeedTransactionState -Path $transactionStatePath -TransactionId $transactionId -Version $Version `
        -PackageName $packageName -Phase 'MarkerCreated' -TemporaryPackage $temporaryPackage -TemporaryManifest $temporaryManifest
    $markerStream = [System.IO.File]::Open($maintenanceMarkerPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $markerBytes = [System.Text.UTF8Encoding]::new($false).GetBytes("feed $transactionId $PID $([DateTimeOffset]::UtcNow.ToString('O'))")
        $markerStream.Write($markerBytes, 0, $markerBytes.Length)
        $markerStream.Flush($true)
        $maintenanceMarkerCreated = $true
    }
    finally { $markerStream.Dispose() }
    # Take the rollback snapshot only after both the global maintenance mutex
    # and this feed transaction marker are held. A second publisher cannot
    # otherwise race between this snapshot and replacement, causing rollback
    # to delete a pair that it did not observe before entering the lock.
    $hadPackage = Test-Path -LiteralPath $destinationPackage -PathType Leaf
    $hadManifest = Test-Path -LiteralPath $destinationManifest -PathType Leaf
    $previousPackageSha256 = if ($hadPackage) { Get-FeedSha256 -Path $destinationPackage } else { $null }
    $previousManifestSha256 = if ($hadManifest) { Get-FeedSha256 -Path $destinationManifest } else { $null }
    if ($hadPackage) { Copy-DurableFile -Source $destinationPackage -Destination $backupPackage -TransactionId $transactionId }
    if ($hadManifest) { Copy-DurableFile -Source $destinationManifest -Destination $backupManifest -TransactionId $transactionId }
    Copy-DurableFile -Source $packageSource -Destination $temporaryPackage -TransactionId $transactionId
    Copy-DurableFile -Source $manifestSource -Destination $temporaryManifest -TransactionId $transactionId
    $copiedPackageSha256 = Get-FeedSha256 -Path $temporaryPackage
    $copiedManifestSha256 = Get-FeedSha256 -Path $temporaryManifest
    if ($copiedPackageSha256 -ne $actualHash -or $copiedManifestSha256 -ne $validatedManifestSha256) {
        throw 'The stable feed source changed after signature and package validation.'
    }
    $manifestSha256 = $validatedManifestSha256
    Write-FeedTransactionState -Path $transactionStatePath -TransactionId $transactionId -Version $Version `
        -PackageName $packageName -Phase 'BackupsReady' -SnapshotReady $true -HadPackage $hadPackage -HadManifest $hadManifest `
        -PackageSha256 $actualHash -ManifestSha256 $manifestSha256 `
        -PreviousPackageSha256 $previousPackageSha256 `
        -PreviousManifestSha256 $previousManifestSha256 `
        -TemporaryPackage $temporaryPackage -TemporaryManifest $temporaryManifest
    $transactionStarted = $true
    Write-FeedTransactionState -Path $transactionStatePath -TransactionId $transactionId -Version $Version `
        -PackageName $packageName -Phase 'ReplacementStarted' -SnapshotReady $true -HadPackage $hadPackage -HadManifest $hadManifest `
        -PackageSha256 $actualHash -ManifestSha256 $manifestSha256 `
        -PreviousPackageSha256 $previousPackageSha256 `
        -PreviousManifestSha256 $previousManifestSha256 `
        -TemporaryPackage $temporaryPackage -TemporaryManifest $temporaryManifest
    Move-Item -LiteralPath $temporaryPackage -Destination $destinationPackage -Force
    Move-Item -LiteralPath $temporaryManifest -Destination $destinationManifest -Force
    if ((Get-FileHash -LiteralPath $destinationPackage -Algorithm SHA256).Hash -ne $actualHash -or
         (Get-FileHash -LiteralPath $destinationManifest -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $manifestSource -Algorithm SHA256).Hash) {
        throw 'The installed stable feed pair failed post-replacement verification.'
    }
    Write-FeedTransactionState -Path $transactionStatePath -TransactionId $transactionId -Version $Version `
        -PackageName $packageName -Phase 'Published' -SnapshotReady $true -HadPackage $hadPackage -HadManifest $hadManifest `
        -PackageSha256 $actualHash -ManifestSha256 $manifestSha256 `
        -PreviousPackageSha256 $previousPackageSha256 `
        -PreviousManifestSha256 $previousManifestSha256 `
        -TemporaryPackage $temporaryPackage -TemporaryManifest $temporaryManifest
    $publicationVerified = $true
}
catch {
    $failure = $_.Exception.Message
    if (-not $transactionStarted) {
        if (@($feedReplacementUnresolvedPaths).Count -gt 0) {
            throw "Stable feed replacement did not start and recovery evidence was preserved at $(@($feedReplacementUnresolvedPaths) -join ', '). $failure"
        }
        # Marker, transaction root, and temporary copies are transaction-owned
        # even when backup/temp publication fails before replacement starts.
        # Mark cleanup as safe only when no replacement backup needs recovery.
        $preTransactionCleanupRequired = $true
        throw "Stable feed replacement did not start. $failure"
    }
    try {
        if ($hadPackage) {
            if (-not (Test-Path -LiteralPath $backupPackage -PathType Leaf) -or
                (Get-FeedSha256 -Path $backupPackage) -ne $previousPackageSha256) {
                throw 'The previous stable package backup is missing or corrupt.'
            }
            if (Test-Path -LiteralPath $destinationPackage -PathType Leaf) {
                Remove-FeedReplacementBackup -Destination $destinationPackage -TransactionId $transactionId
            }
            Copy-DurableFile -Source $backupPackage -Destination $destinationPackage -TransactionId $transactionId
        }
        elseif (Test-Path -LiteralPath $destinationPackage) { Remove-Item -LiteralPath $destinationPackage -Force }
        if ($hadManifest) {
            if (-not (Test-Path -LiteralPath $backupManifest -PathType Leaf) -or
                (Get-FeedSha256 -Path $backupManifest) -ne $previousManifestSha256) {
                throw 'The previous stable manifest backup is missing or corrupt.'
            }
            if (Test-Path -LiteralPath $destinationManifest -PathType Leaf) {
                Remove-FeedReplacementBackup -Destination $destinationManifest -TransactionId $transactionId
            }
            Copy-DurableFile -Source $backupManifest -Destination $destinationManifest -TransactionId $transactionId
        }
        elseif (Test-Path -LiteralPath $destinationManifest) { Remove-Item -LiteralPath $destinationManifest -Force }
        if (($hadPackage -and (Get-FileHash -LiteralPath $destinationPackage -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $backupPackage -Algorithm SHA256).Hash) -or
            ($hadManifest -and (Get-FileHash -LiteralPath $destinationManifest -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $backupManifest -Algorithm SHA256).Hash)) {
            throw 'The previous stable feed pair could not be verified after restore.'
        }
        Remove-FeedReplacementBackup -Destination $destinationPackage -TransactionId $transactionId
        Remove-FeedReplacementBackup -Destination $destinationManifest -TransactionId $transactionId
        $script:feedReplacementUnresolvedPaths = @()
        $rollbackVerified = $true
    }
    catch {
        throw "Stable feed replacement failed and rollback was incomplete: $($_.Exception.Message) Original: $failure"
    }
    throw "Stable feed replacement failed; the previous stable feed pair was restored. $failure"
}
finally {
    if (($publicationVerified -or $rollbackVerified -or $preTransactionCleanupRequired) -and
        @($feedReplacementUnresolvedPaths).Count -eq 0) {
        foreach ($temporary in @($temporaryPackage, $temporaryManifest)) {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
        }
        if ($maintenanceMarkerCreated -and (Test-Path -LiteralPath $maintenanceMarkerPath -PathType Leaf)) {
            $markerContent = Get-Content -LiteralPath $maintenanceMarkerPath -Raw
            if ($markerContent -match "^feed $([regex]::Escape($transactionId)) ") {
                Remove-Item -LiteralPath $maintenanceMarkerPath -Force
            }
        }
        if ($publicationVerified -or $rollbackVerified) {
            Remove-FeedReplacementBackup -Destination $destinationPackage -TransactionId $transactionId
            Remove-FeedReplacementBackup -Destination $destinationManifest -TransactionId $transactionId
        }
        if (Test-Path -LiteralPath $transactionRoot) { Remove-Item -LiteralPath $transactionRoot -Recurse -Force }
    }
    if ($maintenanceMutexHeld) { $maintenanceMutex.ReleaseMutex() }
    $maintenanceMutex.Dispose()
}

Write-Host "Trusted stable update $Version installed. Open Operations and select Check now."
