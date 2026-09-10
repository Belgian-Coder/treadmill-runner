[CmdletBinding()]
param(
  [string]$PlanPath,
  [Parameter(Mandatory)][string]$InstallRoot,
  [Parameter(Mandatory)][string]$DataRoot,
  [ValidatePattern('^http://(127\.0\.0\.1|localhost)(:\d+)?/')][string]$HealthUrl = 'http://127.0.0.1:5180/health/ready',
  [switch]$InfrastructureRefresh,
  [string]$RefreshTransactionId,
  [string]$RefreshNewReleasePath,
  [string]$RefreshIncomingPath,
  [string]$RefreshPreviousImagePath,
  [string]$RefreshDatabaseBackupPath,
  [string]$RefreshJournalPath,
  [string]$RefreshMaintenanceMarkerPath,
  [string]$RefreshHelperPath,
  [string]$RefreshGuardianPath,
  [string]$RefreshReadyPath,
  [string]$RefreshReadyToken,
  [string]$RefreshStartPath,
  [string]$RefreshStartToken,
  [int]$ParentProcessId = 0,
  [string]$RefreshCompletionPath,
  [string]$RefreshCompletionToken,
  [string]$RefreshOwnershipPath,
  [string]$RefreshOwnershipToken,
  [string]$RefreshDatabaseMutationPath,
  [string]$RefreshDatabaseMutationToken,
  [ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$RefreshExpectedHelperHash,
  [ValidatePattern('^[0-9A-Fa-f]{64}$')][string]$RefreshExpectedGuardianHash,
  [string]$RefreshExpectedVersion,
  [string]$RefreshPreconditionPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-DurableTextFile {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Content,
    [string]$ReplacementBackupPath
  )
  $temporary = "$Path.tmp"
  $payload = [System.Text.UTF8Encoding]::new($false).GetBytes($Content)
  $stream = [System.IO.FileStream]::new(
    $temporary,
    [System.IO.FileMode]::Create,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None,
    4096,
    [System.IO.FileOptions]::WriteThrough)
  try {
    $stream.Write($payload, 0, $payload.Length)
    $stream.Flush($true)
  }
  finally { $stream.Dispose() }
  if ([System.IO.File]::Exists($Path)) {
    if ([string]::IsNullOrWhiteSpace($ReplacementBackupPath)) {
      $ReplacementBackupPath = "$Path.replace-backup"
    }
    Replace-DurableFile -Source $temporary -Destination $Path -ReplacementBackupPath $ReplacementBackupPath
  }
  else { [System.IO.File]::Move($temporary, $Path) }
}

function Replace-DurableFile {
  param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Destination,
    [Parameter(Mandatory)][string]$ReplacementBackupPath
  )

  # Windows PowerShell binds a null File.Replace backup argument as an empty
  # path. Keep the atomic same-volume replacement, but require a deterministic
  # transaction-owned backup path. The backup is retained on failure so startup
  # recovery can distinguish a partial swap from a clean transaction.
  if ([string]::IsNullOrWhiteSpace($ReplacementBackupPath)) {
    throw 'The durable replacement backup path is empty.'
  }
  if ([System.IO.File]::Exists($ReplacementBackupPath)) {
    throw "The durable replacement backup already exists: $ReplacementBackupPath"
  }
  $replacementSucceeded = $false
  try {
    [System.IO.File]::Replace($Source, $Destination, $ReplacementBackupPath, $true)
    $replacementSucceeded = $true
  }
  catch {
    $replacementError = $_
    # ReplaceFile can report failure after moving the old destination to its
    # backup. Restore only when the destination is definitely absent; if both
    # paths exist, preserve both artifacts for transaction recovery.
    if (-not [System.IO.File]::Exists($Destination) -and [System.IO.File]::Exists($ReplacementBackupPath)) {
      try {
        [System.IO.File]::Move($ReplacementBackupPath, $Destination)
      }
      catch {
        throw "Durable replacement failed and its destination could not be restored: $($_.Exception.Message)"
      }
    }
    throw $replacementError
  }
  finally {
    if ($replacementSucceeded) {
      try {
        if ([System.IO.File]::Exists($ReplacementBackupPath)) {
          [System.IO.File]::Delete($ReplacementBackupPath)
        }
      }
      catch {
        # The replacement is complete. Leave a named artifact if cleanup is
        # interrupted so terminal recovery can remove it safely.
      }
    }
  }
}

function Copy-DurableFile {
  param(
    [Parameter(Mandatory)][string]$Source,
    [Parameter(Mandatory)][string]$Destination,
    [string]$ReplacementBackupPath
  )
  $temporary = "$Destination.write-tmp"
  $sourceStream = [System.IO.File]::Open($Source, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
  $destinationStream = [System.IO.FileStream]::new(
    $temporary,
    [System.IO.FileMode]::Create,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None,
    65536,
    [System.IO.FileOptions]::WriteThrough)
  try {
    $sourceStream.CopyTo($destinationStream)
    $destinationStream.Flush($true)
  }
  finally {
    $destinationStream.Dispose()
    $sourceStream.Dispose()
  }
  if ([System.IO.File]::Exists($Destination)) {
    if ([string]::IsNullOrWhiteSpace($ReplacementBackupPath)) {
      throw 'An existing durable copy destination requires a transaction-owned replacement backup path.'
    }
    Replace-DurableFile -Source $temporary -Destination $Destination -ReplacementBackupPath $ReplacementBackupPath
  }
  else { [System.IO.File]::Move($temporary, $Destination) }
}

function Reconcile-TransactionSwap {
  param(
    [Parameter(Mandatory)][string]$Destination,
    [Parameter(Mandatory)][string]$ReplacementBackupPath,
    [Parameter(Mandatory)][string]$DurableSourcePath,
    [Parameter(Mandatory)][string]$ExpectedHash
  )
  if (-not (Test-Path -LiteralPath $ReplacementBackupPath -PathType Leaf)) { return }
  if (-not (Test-Path -LiteralPath $DurableSourcePath -PathType Leaf) -or
      (Get-FileSha256 -Path $DurableSourcePath) -ne $ExpectedHash.ToUpperInvariant()) {
    throw "The verified durable rollback source is unavailable or has an unexpected hash: $DurableSourcePath"
  }
  if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
    Copy-DurableFile -Source $DurableSourcePath -Destination $Destination
    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
        (Get-FileSha256 -Path $Destination) -ne $ExpectedHash.ToUpperInvariant()) {
      throw "The transaction replacement backup could not restore its missing destination: $Destination"
    }
    Remove-Item -LiteralPath $ReplacementBackupPath -Force -ErrorAction Stop
    return
  }
  if ((Get-FileSha256 -Path $Destination) -eq $ExpectedHash.ToUpperInvariant()) {
    Remove-Item -LiteralPath $ReplacementBackupPath -Force -ErrorAction Stop
    return
  }
  # The durable rollback source is verified before the ambiguous swap is
  # removed. Replacing the existing destination recreates the same exact swap
  # path, and Copy-DurableFile removes it only after the replacement succeeds.
  Remove-Item -LiteralPath $ReplacementBackupPath -Force -ErrorAction Stop
  Copy-DurableFile -Source $DurableSourcePath -Destination $Destination -ReplacementBackupPath $ReplacementBackupPath
  if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
      (Get-FileSha256 -Path $Destination) -ne $ExpectedHash.ToUpperInvariant()) {
    throw "The durable rollback source did not restore its destination: $Destination"
  }
}

function Assert-UnderRoot {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Root)
  $resolvedPath = [System.IO.Path]::GetFullPath($Path)
  $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
  $prefix = $resolvedRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
  if (-not $resolvedPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "An update path escapes its configured root."
  }
  return $resolvedPath
}

function Assert-NoReparsePoint {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$StopAt)
  $cursor = [System.IO.Path]::GetFullPath($Path)
  $root = [System.IO.Path]::GetFullPath($StopAt)
  while ($cursor.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    if (Test-Path -LiteralPath $cursor) {
      $item = Get-Item -LiteralPath $cursor -Force
      if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Reparse points are not allowed in update transaction paths.'
      }
    }
    if ($cursor -eq $root) { break }
    $cursor = Split-Path -Parent $cursor
  }
}

function Write-Journal {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$State, [string]$Reason)
  Write-JournalPayload -Path $Path -TransactionId ([string]$plan.TransactionId) -Version ([string]$plan.Version) -State $State -Reason $Reason
}

function Read-TransactionJournal {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$Version
  )
  $journal = $null
  try { $journal = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
  catch { throw "The transaction journal is invalid: $Path" }
  if ([int]$journal.schemaVersion -ne 1 -or
      [string]$journal.transactionId -ne $TransactionId -or
      [string]$journal.version -ne $Version -or
      [string]$journal.state -notin @('Activating', 'Activated', 'RolledBack', 'RollbackFailed')) {
    throw "The transaction journal does not match its transaction: $Path"
  }
  return $journal
}

function Reconcile-JournalSwap {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$Version
  )
  $journalSwap = "$Path.replace-backup"
  if (-not (Test-Path -LiteralPath $journalSwap -PathType Leaf)) { return }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    # Validate the surviving swap before publishing it as the journal.
    Read-TransactionJournal -Path $journalSwap -TransactionId $TransactionId -Version $Version | Out-Null
    [System.IO.File]::Move($journalSwap, $Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
      throw 'The journal replacement backup could not restore the missing journal.'
    }
    Read-TransactionJournal -Path $Path -TransactionId $TransactionId -Version $Version | Out-Null
    return
  }
  # Both artifacts exist. Only remove the swap after validating the current
  # journal; an invalid or foreign pair remains available for recovery.
  Read-TransactionJournal -Path $Path -TransactionId $TransactionId -Version $Version | Out-Null
  Remove-Item -LiteralPath $journalSwap -Force -ErrorAction Stop
}

function Write-JournalPayload {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$State,
    [string]$Reason
  )
  $payload = [ordered]@{
    schemaVersion = 1
    transactionId = $TransactionId
    version = $Version
    state = $State
    occurredAtUtc = [DateTimeOffset]::UtcNow.ToString('O')
    reason = $Reason
  } | ConvertTo-Json
  $journalMutex = New-MaintenanceMutex
  $journalMutexHeld = $false
  try {
    if (-not (Wait-MaintenanceMutex -Mutex $journalMutex -TimeoutMilliseconds 30000)) {
      throw 'The journal replacement could not acquire the maintenance lock.'
    }
    $journalMutexHeld = $true
    Reconcile-JournalSwap -Path $Path -TransactionId $TransactionId -Version $Version
    Write-DurableTextFile -Path $Path -Content $payload
  }
  finally {
    if ($journalMutexHeld) { $journalMutex.ReleaseMutex() }
    $journalMutex.Dispose()
  }
}

function Wait-ReleaseHealth {
  param([Parameter(Mandatory)][string]$HealthUrl, [string]$ExpectedVersion)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    try {
      $ready = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3
      if ($ready.StatusCode -eq 200) {
        if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) { return $true }
        $statusUrl = ([Uri]::new([Uri]$HealthUrl, '/api/updates/status')).AbsoluteUri
        $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 3
        if ([string]$status.currentVersion -eq $ExpectedVersion) { return $true }
      }
    }
    catch { }
    Start-Sleep -Seconds 2
  }
  return $false
}

function Set-ServiceBinary {
  param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$ImagePath)
  & sc.exe config $Name 'binPath=' $ImagePath | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'The service binary path could not be changed.' }
}

function Get-FileSha256 {
  param([Parameter(Mandatory)][string]$Path)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $stream = [System.IO.File]::OpenRead($Path)
  try {
    return ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '')
  }
  finally {
    $stream.Dispose()
    $sha.Dispose()
  }
}

function Get-ServiceExecutablePath {
  param([Parameter(Mandatory)][string]$ImagePath)
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

function Assert-ExactPath {
  param(
    [Parameter(Mandatory)][string]$Actual,
    [Parameter(Mandatory)][string]$Expected,
    [Parameter(Mandatory)][string]$Name
  )
  $actualFull = [System.IO.Path]::GetFullPath($Actual)
  $expectedFull = [System.IO.Path]::GetFullPath($Expected)
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualFull, $expectedFull)) {
    throw "$Name is outside its fixed update path contract."
  }
  return $expectedFull
}

function Write-RefreshReady {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Token)
  Write-DurableTextFile -Path $Path -Content $Token
}

function Wait-RefreshChildReady {
  param(
    [Parameter(Mandatory)]$Process,
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Token,
    [int]$TimeoutSeconds = 30
  )
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      $observed = (Get-Content -LiteralPath $Path -Raw).Trim()
      if ([System.StringComparer]::Ordinal.Equals($observed, $Token)) { return $true }
      throw 'The protected updater child produced an invalid handoff token.'
    }
    if ($Process.HasExited) { throw 'The protected updater child exited before accepting the handoff.' }
    Start-Sleep -Milliseconds 200
  }
  throw 'The protected updater child did not accept the handoff within 30 seconds.'
}

function Wait-RefreshChildOwnership {
  param(
    [Parameter(Mandatory)]$Process,
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Token,
    [int]$TimeoutSeconds = 30
  )
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      $observed = (Get-Content -LiteralPath $Path -Raw).Trim()
      if (-not [System.StringComparer]::Ordinal.Equals($observed, $Token)) {
        throw 'The protected updater child produced an invalid ownership token.'
      }
      return $true
    }
    if ($Process.HasExited) { throw 'The protected updater child exited before claiming ownership.' }
    Start-Sleep -Milliseconds 200
  }
  throw 'The protected updater child did not claim the maintenance lock within 30 seconds.'
}

function Wait-RefreshStartSignal {
  param(
    [Parameter(Mandatory)][int]$ParentProcessId,
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Token,
    [int]$TimeoutSeconds = 180
  )
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      $observed = (Get-Content -LiteralPath $Path -Raw).Trim()
      if ([System.StringComparer]::Ordinal.Equals($observed, $Token)) { return $true }
      throw 'The updater supervisor produced an invalid transaction-start token.'
    }
    if ($ParentProcessId -gt 0 -and $null -eq (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) {
      throw 'The updater supervisor exited before signaling the transaction start.'
    }
    Start-Sleep -Milliseconds 250
  }
  throw "The updater supervisor did not signal transaction start within $TimeoutSeconds seconds."
}

function Read-RefreshTerminalState {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$ExpectedVersion
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  try {
    $journal = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ([int]$journal.schemaVersion -ne 1 -or
        [string]$journal.transactionId -ne $TransactionId -or
        [string]$journal.version -ne $ExpectedVersion) {
      return $null
    }
    $state = [string]$journal.state
    if ($state -notin @('Activated', 'RolledBack', 'RollbackFailed')) { return $null }
    return $state
  }
  catch {
    return $null
  }
}

function Wait-RefreshCompletionAcknowledgement {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Token,
    [int]$TimeoutSeconds = 180
  )
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
      $observed = (Get-Content -LiteralPath $Path -Raw).Trim()
      if ([System.StringComparer]::Ordinal.Equals($observed, $Token)) { return $true }
      throw 'The updater supervisor produced an invalid completion token.'
    }
    Start-Sleep -Milliseconds 250
  }
  throw "The updater supervisor did not acknowledge completion within $TimeoutSeconds seconds."
}

function Wait-RefreshChildTerminal {
  param(
    [Parameter(Mandatory)]$Process,
    [Parameter(Mandatory)][string]$JournalPath,
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$ExpectedVersion,
    [Parameter(Mandatory)][string]$HealthUrl,
    [Parameter(Mandatory)][string]$CompletionPath,
    [Parameter(Mandatory)][string]$CompletionToken,
    [int]$TimeoutSeconds = 180
  )
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  $activationAcknowledged = $false
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $Process.Refresh()
    $state = Read-RefreshTerminalState -Path $JournalPath -TransactionId $TransactionId -ExpectedVersion $ExpectedVersion
    if ($state -eq 'Activated' -and -not $activationAcknowledged) {
      # The child writes Activated only after checking the expected version,
      # readiness, and protected updater hashes. A parent-side transient probe
      # must not reverse that durable terminal decision.
      Write-RefreshReady -Path $CompletionPath -Token $CompletionToken | Out-Null
      $activationAcknowledged = $true
      # The journal, health check, and completion acknowledgement are the
      # activation contract. Give the child a bounded opportunity to exit and
      # finish hygiene. The parent must keep the completion token and rollback
      # backups until the child exits; otherwise a slow child could miss the
      # token and attempt rollback after the parent has deleted its evidence.
    }
    if ($Process.HasExited) {
      if ($state -eq 'RolledBack') { return 'RolledBack' }
      # Activated is authoritative once the child has observed the completion
      # acknowledgement. Cleanup is best-effort and must never turn an
      # already healthy activation into a rollback attempt after backups or
      # markers have been removed.
      if ($state -eq 'Activated' -and $activationAcknowledged) { return 'Activated' }
      if ($Process.ExitCode -eq 0) {
        if ($state -ne 'Activated' -or -not $activationAcknowledged) { throw 'The protected updater child exited successfully without an acknowledged Activated journal.' }
        return 'Activated'
      }
      throw "The protected updater child exited without a safe terminal state (journal state: $state)."
    }
    Start-Sleep -Milliseconds 250
  }
  # Reconcile once more outside the timed loop. The child can publish its
  # durable terminal journal between the final poll and the deadline check.
  # Missing that write here would route a completed activation into rollback.
  $terminalState = Read-RefreshTerminalState -Path $JournalPath -TransactionId $TransactionId -ExpectedVersion $ExpectedVersion
  if ($terminalState -eq 'RolledBack') { return 'RolledBack' }
  if ($terminalState -eq 'Activated' -and -not $activationAcknowledged) {
    Write-RefreshReady -Path $CompletionPath -Token $CompletionToken | Out-Null
    $activationAcknowledged = $true
  }
  if ($activationAcknowledged) {
    # Activated plus verified health is authoritative. A child that remains
    # alive after the bounded acknowledgement window must be stopped before
    # parent-owned cleanup removes its completion token and backups, so it can
    # never wake later and start an impossible rollback.
    if (-not $Process.HasExited) {
      Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
      Wait-Process -Id $Process.Id -Timeout 10 -ErrorAction SilentlyContinue
      $Process.Refresh()
      if (-not $Process.HasExited) {
        throw 'The protected updater child could not be stopped after its completion deadline; recovery artifacts were preserved.'
      }
    }
    $terminalState = Read-RefreshTerminalState -Path $JournalPath -TransactionId $TransactionId -ExpectedVersion $ExpectedVersion
    if ($terminalState -eq 'Activated') {
      return 'Activated'
    }
  }
  throw "The protected updater child did not complete within $TimeoutSeconds seconds."
}

function Assert-ServiceUsesImagePath {
  param([Parameter(Mandatory)][string]$ServiceName, [Parameter(Mandatory)][string]$ExpectedImagePath)
  $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
  if ($null -eq $service) { throw "The $ServiceName service could not be inspected after rollback." }
  $actual = Get-ServiceExecutablePath -ImagePath ([string]$service.PathName)
  $expected = Get-ServiceExecutablePath -ImagePath $ExpectedImagePath
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actual, $expected)) {
    throw 'The previous release service image was not restored before cleanup.'
  }
}

function Restore-ServiceImageSafely {
  param(
    [Parameter(Mandatory)][string]$ServiceName,
    [Parameter(Mandatory)][string]$TargetImagePath,
    [Parameter(Mandatory)][string]$PreviousImagePath
  )
  $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
  if ($null -eq $service) { throw "The $ServiceName service is missing during rollback." }
  $actualExecutable = Get-ServiceExecutablePath -ImagePath ([string]$service.PathName)
  $targetExecutable = Get-ServiceExecutablePath -ImagePath $TargetImagePath
  $previousExecutable = Get-ServiceExecutablePath -ImagePath $PreviousImagePath
  if ([System.StringComparer]::OrdinalIgnoreCase.Equals($actualExecutable, $previousExecutable)) {
    # A previous recovery attempt already restored the old image. Avoid a
    # second rewrite because this transaction no longer owns that transition.
    return
  }
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($actualExecutable, $targetExecutable)) {
    throw 'The current gateway service image changed outside this update transaction; refusing to restore the previous image.'
  }
  Set-ServiceBinary -Name $ServiceName -ImagePath $PreviousImagePath
  Assert-ServiceUsesImagePath -ServiceName $ServiceName -ExpectedImagePath $PreviousImagePath
}

function New-MaintenanceMutex {
  return [System.Threading.Mutex]::new($false, 'Global\TreadmillRunnerGateway.Maintenance')
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
    # Treat that as a successful acquisition; callers still validate their
    # marker and plan state before taking any recovery action.
    return $true
  }
}

function Remove-OwnedMaintenanceMarker {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$TransactionId)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
  $content = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
  if ($content -notmatch "^update $([regex]::Escape($TransactionId)) ") {
    throw 'The maintenance marker is owned by another transaction.'
  }
  Remove-Item -LiteralPath $Path -Force
}

function Complete-ActivatedParentCleanup {
  param(
    [Parameter(Mandatory)][string]$JournalPath,
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$ExpectedVersion,
    [Parameter(Mandatory)][string]$PlanPath,
    [Parameter(Mandatory)][string]$MaintenanceMarkerPath,
    [Parameter(Mandatory)][string[]]$OwnedArtifactPaths
  )
  $cleanupMutex = New-MaintenanceMutex
  $cleanupMutexHeld = $false
  try {
    if (-not (Wait-MaintenanceMutex -Mutex $cleanupMutex -TimeoutMilliseconds 30000)) {
      throw 'The parent could not reacquire the maintenance lock for terminal activation cleanup.'
    }
    $cleanupMutexHeld = $true
    $terminalState = Read-RefreshTerminalState -Path $JournalPath -TransactionId $TransactionId -ExpectedVersion $ExpectedVersion
    if ($terminalState -ne 'Activated') {
      throw 'The activation journal no longer records the expected Activated transaction.'
    }
    if (Test-Path -LiteralPath $PlanPath -PathType Leaf) {
      $planOnDisk = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
      if ([string]$planOnDisk.TransactionId -ne $TransactionId -or [string]$planOnDisk.Version -ne $ExpectedVersion) {
        throw 'The pending activation plan is not owned by the Activated transaction.'
      }
    }
    if (Test-Path -LiteralPath $MaintenanceMarkerPath -PathType Leaf) {
      $marker = Get-Content -LiteralPath $MaintenanceMarkerPath -Raw
      if ($marker -notmatch "^update $([regex]::Escape($TransactionId)) ") {
        throw 'The maintenance marker is not owned by the Activated transaction.'
      }
    }
    Remove-OwnedMaintenanceMarker -Path $MaintenanceMarkerPath -TransactionId $TransactionId
    if (Test-Path -LiteralPath $PlanPath -PathType Leaf) { Remove-Item -LiteralPath $PlanPath -Force }
    foreach ($artifactPath in $OwnedArtifactPaths) {
      if (-not [string]::IsNullOrWhiteSpace($artifactPath)) {
        if (Test-Path -LiteralPath $artifactPath -PathType Container) {
          Remove-Item -LiteralPath $artifactPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        else {
          Remove-Item -LiteralPath $artifactPath -Force -ErrorAction SilentlyContinue
        }
      }
    }
  }
  finally {
    if ($cleanupMutexHeld) { $cleanupMutex.ReleaseMutex() }
    $cleanupMutex.Dispose()
  }
}

function Remove-FailedRelease {
  param(
    [Parameter(Mandatory)][string]$NewReleasePath,
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$PreviousImagePath
  )
  $resolvedNew = Assert-UnderRoot -Path $NewReleasePath -Root $ReleaseRoot
  Assert-NoReparsePoint -Path $resolvedNew -StopAt $ReleaseRoot
  $previousExecutable = Get-ServiceExecutablePath -ImagePath $PreviousImagePath
  if ([System.StringComparer]::OrdinalIgnoreCase.Equals($resolvedNew, (Split-Path -Parent $previousExecutable))) {
    throw 'The failed release path is still the service current release.'
  }
  if (Test-Path -LiteralPath $resolvedNew) {
    Remove-Item -LiteralPath $resolvedNew -Recurse -Force
  }
  if (Test-Path -LiteralPath $resolvedNew) {
    throw 'The failed promoted release could not be removed after rollback.'
  }
}

function Invoke-StaleUpdateRecovery {
  param(
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$ExpectedVersion,
    [Parameter(Mandatory)][string]$PlanPath,
    [Parameter(Mandatory)][string]$JournalPath,
    [Parameter(Mandatory)][string]$MaintenanceMarkerPath,
    [Parameter(Mandatory)][string]$PreconditionPath,
    [Parameter(Mandatory)][string]$IncomingPath,
    [Parameter(Mandatory)][string]$NewReleasePath,
    [Parameter(Mandatory)][string]$DatabasePath,
    [Parameter(Mandatory)][string]$DatabaseBackupPath,
    [Parameter(Mandatory)][string]$HelperPath,
    [Parameter(Mandatory)][string]$GuardianPath,
    [Parameter(Mandatory)][string]$UpdaterRoot,
    [Parameter(Mandatory)][string]$ReleaseRoot,
    [Parameter(Mandatory)][string]$HealthUrl,
    [Parameter(Mandatory)][string]$ServiceName
  )
  $dataRoot = Split-Path -Parent (Split-Path -Parent $DatabasePath)
  $markerPathExists = Test-Path -LiteralPath $MaintenanceMarkerPath
  if ($markerPathExists) {
    Assert-UnderRoot -Path $MaintenanceMarkerPath -Root $dataRoot | Out-Null
    Assert-NoReparsePoint -Path $MaintenanceMarkerPath -StopAt $dataRoot
    if (-not (Test-Path -LiteralPath $MaintenanceMarkerPath -PathType Leaf)) {
      throw 'The maintenance marker path is not a regular file.'
    }
  }
  $markerExists = $markerPathExists
  $journalExists = Test-Path -LiteralPath $JournalPath -PathType Leaf
  $journalSwapExists = Test-Path -LiteralPath ("$JournalPath.replace-backup") -PathType Leaf
  if (-not $markerExists -and -not $journalExists -and -not $journalSwapExists) { return $false }
  $helperBackup = Join-Path $UpdaterRoot ".update-helper-$TransactionId.backup"
  $guardianBackup = Join-Path $UpdaterRoot ".service-guardian-$TransactionId.backup"
  $helperStage = Join-Path $UpdaterRoot ".update-helper-$TransactionId.tmp"
  $guardianStage = Join-Path $UpdaterRoot ".service-guardian-$TransactionId.tmp"
  $readyPath = Join-Path $UpdaterRoot ".update-ready-$TransactionId.token"
  $startPath = Join-Path $UpdaterRoot ".update-start-$TransactionId.token"
  $completionPath = Join-Path $UpdaterRoot ".update-completion-$TransactionId.token"
  $ownershipPath = Join-Path $UpdaterRoot ".update-ownership-$TransactionId.token"
  $databaseMutationPath = Join-Path $UpdaterRoot ".update-database-$TransactionId.token"
  $databaseSwap = "$DatabasePath.update-$TransactionId.replace-backup"
  $helperBackupSwap = "$helperBackup.replace-backup"
  $guardianBackupSwap = "$guardianBackup.replace-backup"
  $helperTargetSwap = "$HelperPath.update-$TransactionId.replace-backup"
  $guardianTargetSwap = "$GuardianPath.update-$TransactionId.replace-backup"
  $preStartArtifacts = @(
    $IncomingPath, $NewReleasePath, $PreconditionPath,
    $helperBackup, $guardianBackup, $helperStage, $guardianStage,
    $readyPath, $startPath, $completionPath, $ownershipPath, $databaseMutationPath,
    $databaseSwap, $helperBackupSwap, $guardianBackupSwap, $helperTargetSwap, $guardianTargetSwap,
    "$PlanPath.replace-backup", "$PreconditionPath.replace-backup",
    "$helperBackup.write-tmp", "$guardianBackup.write-tmp",
    "$helperStage.write-tmp", "$guardianStage.write-tmp",
    "$HelperPath.write-tmp", "$GuardianPath.write-tmp", "$DatabasePath.write-tmp",
    "$JournalPath.tmp", "$PlanPath.tmp", "$PreconditionPath.tmp",
    "$readyPath.replace-backup", "$startPath.replace-backup",
    "$completionPath.replace-backup", "$ownershipPath.replace-backup",
    "$databaseMutationPath.replace-backup")
  if ($markerExists) {
    $marker = Get-Content -LiteralPath $MaintenanceMarkerPath -Raw
    if ($marker -notmatch "^update $([regex]::Escape($TransactionId)) parent=(\d+) parentStart=(\d+) child=(\d+) childStart=(\d+) ") {
      throw 'The maintenance marker is not owned by the pending update transaction.'
    }
    foreach ($identity in @(
      @{ Id = [int]$Matches[1]; StartTicks = [long]$Matches[2] },
      @{ Id = [int]$Matches[3]; StartTicks = [long]$Matches[4] }
    )) {
      $owner = Get-Process -Id $identity.Id -ErrorAction SilentlyContinue
      if ($null -ne $owner) {
        try {
          if ($owner.StartTime.ToUniversalTime().Ticks -eq $identity.StartTicks) {
            throw 'The update maintenance marker is owned by an active transaction process.'
          }
        }
        catch {
          if ($_.Exception.Message -eq 'The update maintenance marker is owned by an active transaction process.') { throw }
          throw 'The update transaction process identity could not be validated safely.'
        }
      }
    }
  }
  $ownedArtifacts = @(
    $IncomingPath,
    (Join-Path $UpdaterRoot ".update-ready-$TransactionId.token"),
    $startPath,
    (Join-Path $UpdaterRoot ".update-completion-$TransactionId.token"),
    (Join-Path $UpdaterRoot ".update-ownership-$TransactionId.token"),
    $databaseMutationPath,
    $helperBackup,
    $guardianBackup,
    ("$helperBackup.replace-backup"),
    ("$guardianBackup.replace-backup"),
    $databaseSwap,
    $helperTargetSwap,
    $guardianTargetSwap,
    ("$JournalPath.replace-backup"),
    ("$PreconditionPath.replace-backup"),
    ("$startPath.replace-backup"),
    ("$databaseMutationPath.replace-backup"),
    ("$(Join-Path $UpdaterRoot ".update-ready-$TransactionId.token").replace-backup"),
    ("$(Join-Path $UpdaterRoot ".update-completion-$TransactionId.token").replace-backup"),
    ("$(Join-Path $UpdaterRoot ".update-ownership-$TransactionId.token").replace-backup"),
    $PreconditionPath,
    (Join-Path $UpdaterRoot ".update-helper-$TransactionId.tmp"),
    (Join-Path $UpdaterRoot ".service-guardian-$TransactionId.tmp")
  )
  foreach ($path in $ownedArtifacts) {
    $dataRoot = Split-Path -Parent (Split-Path -Parent $DatabasePath)
    $root = if ($path.StartsWith($ReleaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $ReleaseRoot }
      elseif ($path.StartsWith($UpdaterRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $UpdaterRoot }
      else { $dataRoot }
    Assert-UnderRoot -Path $path -Root $root | Out-Null
    Assert-NoReparsePoint -Path $path -StopAt $root
  }

  $maintenanceMutex = New-MaintenanceMutex
  $maintenanceMutexHeld = $false
  try {
    if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) {
      throw 'The interrupted update is still active; startup recovery did not acquire its maintenance lock.'
    }
    $maintenanceMutexHeld = $true
    # The marker was sampled before waiting for the mutex. Re-read it while
    # owning the lock so a concurrently started update cannot be mistaken for
    # a markerless terminal transaction.
    $markerPathExists = Test-Path -LiteralPath $MaintenanceMarkerPath
    if ($markerPathExists) {
      Assert-UnderRoot -Path $MaintenanceMarkerPath -Root $dataRoot | Out-Null
      Assert-NoReparsePoint -Path $MaintenanceMarkerPath -StopAt $dataRoot
      if (-not (Test-Path -LiteralPath $MaintenanceMarkerPath -PathType Leaf)) {
        throw 'The maintenance marker path is not a regular file.'
      }
    }
    $markerExists = $markerPathExists
    if ($markerExists) {
      $marker = Get-Content -LiteralPath $MaintenanceMarkerPath -Raw
      if ($marker -notmatch "^update $([regex]::Escape($TransactionId)) parent=(\d+) parentStart=(\d+) child=(\d+) childStart=(\d+) ") {
        throw 'The maintenance marker changed before interrupted-update recovery.'
      }
    }

    # Reconcile the journal swap while this recovery transaction owns the
    # maintenance mutex, before classifying the journal's terminal state.
    Reconcile-JournalSwap -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion
    $journal = $null
    if (Test-Path -LiteralPath $JournalPath -PathType Leaf) {
      $journal = Read-TransactionJournal -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion
    }
    if (-not $markerExists -and $null -eq $journal) {
      return $false
    }
    if (-not $markerExists -and [string]$journal.state -notin @('Activated', 'RolledBack')) {
      throw 'An interrupted nonterminal update has no maintenance marker; automatic recovery is unsafe.'
    }

    if (-not $markerExists -and $null -ne $journal -and [string]$journal.state -eq 'RolledBack' -and
        -not (Test-Path -LiteralPath $PreconditionPath -PathType Leaf)) {
      $planOnDisk = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
      if ([string]$planOnDisk.TransactionId -ne $TransactionId -or
          [string]$planOnDisk.Version -ne $ExpectedVersion) {
        throw 'The rolled-back terminal journal does not match the pending activation plan.'
      }
      $dataRoot = Split-Path -Parent (Split-Path -Parent $DatabasePath)
      foreach ($artifact in $preStartArtifacts) {
        $root = if ($artifact.StartsWith($ReleaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $ReleaseRoot }
          elseif ($artifact.StartsWith($UpdaterRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $UpdaterRoot }
          else { $dataRoot }
        Assert-UnderRoot -Path $artifact -Root $root | Out-Null
        Assert-NoReparsePoint -Path $artifact -StopAt $root
        if (Test-Path -LiteralPath $artifact) {
          throw "The rolled-back terminal transaction still has a mutation artifact: $artifact"
        }
      }
      Remove-Item -LiteralPath $PlanPath -Force -ErrorAction Stop
      return $true
    }

    if (-not (Test-Path -LiteralPath $PreconditionPath -PathType Leaf)) {
      throw 'The interrupted update has no durable recovery preconditions.'
    }
    $preconditions = Get-Content -LiteralPath $PreconditionPath -Raw | ConvertFrom-Json
    if ([int]$preconditions.schemaVersion -ne 1 -or
        [string]$preconditions.transactionId -ne $TransactionId -or
        [string]$preconditions.version -ne $ExpectedVersion -or
        [string]$preconditions.previousHelperHash -notmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$preconditions.previousGuardianHash -notmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$preconditions.expectedHelperHash -notmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$preconditions.expectedGuardianHash -notmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$preconditions.startToken -notmatch '^[0-9a-f]{32}$' -or
        [string]$preconditions.databaseMutationToken -notmatch '^[0-9a-f]{32}$' -or
        [int]$preconditions.parentProcessId -le 0 -or
        [long]$preconditions.parentProcessStartTicks -le 0) {
      throw 'The interrupted update recovery preconditions are invalid.'
    }
    $previousImagePath = [string]$preconditions.previousImagePath
    $previousExecutable = Get-ServiceExecutablePath -ImagePath $previousImagePath
    Assert-UnderRoot -Path $previousExecutable -Root $ReleaseRoot | Out-Null
    if ([System.IO.Path]::GetFileName($previousExecutable) -ne 'TreadmillRunner.Gateway.exe') {
      throw 'The interrupted update previous service image is outside the release executable contract.'
    }

    if ($null -ne $journal -and [string]$journal.state -eq 'Activated') {
      Assert-ServiceUsesImagePath -ServiceName $ServiceName -ExpectedImagePath ('"{0}"' -f (Join-Path $NewReleasePath 'TreadmillRunner.Gateway.exe'))
      if ((Get-FileSha256 -Path $HelperPath) -ne ([string]$preconditions.expectedHelperHash).ToUpperInvariant() -or
          (Get-FileSha256 -Path $GuardianPath) -ne ([string]$preconditions.expectedGuardianHash).ToUpperInvariant() -or
          -not (Wait-ReleaseHealth -HealthUrl $HealthUrl -ExpectedVersion $ExpectedVersion)) {
        throw 'The interrupted update journal says Activated, but installed health or protected hashes do not agree.'
      }
    }
    else {
      $transactionStarted = Test-Path -LiteralPath $startPath -PathType Leaf
      if (-not $transactionStarted -and $null -ne $journal -and
          [string]$journal.state -in @('Activating', 'RollbackFailed')) {
        throw 'The interrupted update journal records mutation but its durable start token is missing.'
      }
      if (-not $transactionStarted -and (Test-Path -LiteralPath $NewReleasePath)) {
        throw 'The promoted release exists without a durable transaction-start token.'
      }
      if ($transactionStarted -and
          -not [System.StringComparer]::Ordinal.Equals((Get-Content -LiteralPath $startPath -Raw).Trim(), [string]$preconditions.startToken)) {
        throw 'The interrupted update start token is invalid.'
      }
      if ($transactionStarted) {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        $databaseBackupHash = Get-FileSha256 -Path $DatabaseBackupPath
        Reconcile-TransactionSwap -Destination $DatabasePath -ReplacementBackupPath $databaseSwap -DurableSourcePath $DatabaseBackupPath -ExpectedHash $databaseBackupHash
        Reconcile-TransactionSwap -Destination $helperBackup -ReplacementBackupPath "$helperBackup.replace-backup" -DurableSourcePath $HelperPath -ExpectedHash ([string]$preconditions.previousHelperHash)
        Reconcile-TransactionSwap -Destination $guardianBackup -ReplacementBackupPath "$guardianBackup.replace-backup" -DurableSourcePath $GuardianPath -ExpectedHash ([string]$preconditions.previousGuardianHash)
        Reconcile-TransactionSwap -Destination $HelperPath -ReplacementBackupPath $helperTargetSwap -DurableSourcePath $helperBackup -ExpectedHash ([string]$preconditions.previousHelperHash)
        Reconcile-TransactionSwap -Destination $GuardianPath -ReplacementBackupPath $guardianTargetSwap -DurableSourcePath $guardianBackup -ExpectedHash ([string]$preconditions.previousGuardianHash)
        if (Test-Path -LiteralPath $databaseMutationPath -PathType Leaf) {
          if (-not [System.StringComparer]::Ordinal.Equals((Get-Content -LiteralPath $databaseMutationPath -Raw).Trim(), [string]$preconditions.databaseMutationToken)) {
            throw 'The interrupted update database-mutation token is invalid.'
          }
          Remove-Item -LiteralPath ($DatabasePath + '-wal') -Force -ErrorAction SilentlyContinue
          Remove-Item -LiteralPath ($DatabasePath + '-shm') -Force -ErrorAction SilentlyContinue
          Copy-DurableFile -Source $DatabaseBackupPath -Destination $DatabasePath -ReplacementBackupPath $databaseSwap
          if (-not (Test-Path -LiteralPath $DatabasePath -PathType Leaf) -or
              (Get-FileSha256 -Path $DatabasePath) -ne $databaseBackupHash) {
            throw 'Interrupted-update database recovery failed hash verification.'
          }
          if ((Test-Path -LiteralPath ($DatabasePath + '-wal')) -or (Test-Path -LiteralPath ($DatabasePath + '-shm'))) {
            throw 'Interrupted-update database sidecars remained after recovery.'
          }
        }
        foreach ($restore in @(
          @{ Target = $HelperPath; Backup = $helperBackup; Hash = ([string]$preconditions.previousHelperHash).ToUpperInvariant() },
          @{ Target = $GuardianPath; Backup = $guardianBackup; Hash = ([string]$preconditions.previousGuardianHash).ToUpperInvariant() }
        )) {
          $targetMatches = (Test-Path -LiteralPath $restore.Target -PathType Leaf) -and
            ((Get-FileSha256 -Path $restore.Target) -eq $restore.Hash)
          if (-not $targetMatches) {
            if (-not (Test-Path -LiteralPath $restore.Backup -PathType Leaf) -or (Get-FileSha256 -Path $restore.Backup) -ne $restore.Hash) {
              throw 'An interrupted-update protected-script backup is missing or invalid.'
            }
            $restoreSwap = if ($restore.Target -eq $HelperPath) { $helperTargetSwap } else { $guardianTargetSwap }
            Copy-DurableFile -Source $restore.Backup -Destination $restore.Target -ReplacementBackupPath $restoreSwap
          }
          if (-not (Test-Path -LiteralPath $restore.Target -PathType Leaf) -or
              (Get-FileSha256 -Path $restore.Target) -ne $restore.Hash) { throw 'Interrupted-update protected-script recovery failed.' }
        }
        Set-ServiceBinary -Name $ServiceName -ImagePath $previousImagePath
        Start-Service -Name $ServiceName -ErrorAction Stop
        if (-not (Wait-ReleaseHealth -HealthUrl $HealthUrl -ExpectedVersion '')) {
          throw 'The previous release did not recover readiness after an interrupted update.'
        }
        Assert-ServiceUsesImagePath -ServiceName $ServiceName -ExpectedImagePath $previousImagePath
        Remove-FailedRelease -NewReleasePath $NewReleasePath -ReleaseRoot $ReleaseRoot -PreviousImagePath $previousImagePath
      }
      Write-JournalPayload -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion -State 'RolledBack' -Reason 'Recovered an update transaction interrupted before an authoritative Activated terminal state.'
    }

    $planOnDisk = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
    if ([string]$planOnDisk.TransactionId -ne $TransactionId -or [string]$planOnDisk.Version -ne $ExpectedVersion) {
      throw 'The pending plan changed during interrupted-update recovery.'
    }
    if ($markerExists) { Remove-OwnedMaintenanceMarker -Path $MaintenanceMarkerPath -TransactionId $TransactionId }
    Remove-Item -LiteralPath $PlanPath -Force
    foreach ($artifact in $ownedArtifacts) {
      if (Test-Path -LiteralPath $artifact -PathType Container) { Remove-Item -LiteralPath $artifact -Recurse -Force -ErrorAction SilentlyContinue }
      else { Remove-Item -LiteralPath $artifact -Force -ErrorAction SilentlyContinue }
    }
    return $true
  }
  finally {
    if ($maintenanceMutexHeld) { $maintenanceMutex.ReleaseMutex() }
    $maintenanceMutex.Dispose()
  }
}

function Invoke-InfrastructureRefresh {
  param(
    [Parameter(Mandatory)][string]$PlanPath,
    [Parameter(Mandatory)][string]$TransactionId,
    [Parameter(Mandatory)][string]$NewReleasePath,
    [Parameter(Mandatory)][string]$PreviousImagePath,
    [Parameter(Mandatory)][string]$DatabaseBackupPath,
    [Parameter(Mandatory)][string]$IncomingPath,
    [Parameter(Mandatory)][string]$JournalPath,
    [Parameter(Mandatory)][string]$MaintenanceMarkerPath,
    [Parameter(Mandatory)][string]$HelperPath,
    [Parameter(Mandatory)][string]$GuardianPath,
    [Parameter(Mandatory)][string]$ExpectedHelperHash,
    [Parameter(Mandatory)][string]$ExpectedGuardianHash,
    [Parameter(Mandatory)][string]$PreconditionPath,
    [Parameter(Mandatory)][string]$ReadyPath,
    [Parameter(Mandatory)][string]$ReadyToken,
    [Parameter(Mandatory)][string]$StartPath,
    [Parameter(Mandatory)][string]$StartToken,
    [Parameter(Mandatory)][string]$CompletionPath,
    [Parameter(Mandatory)][string]$CompletionToken,
    [Parameter(Mandatory)][string]$OwnershipPath,
    [Parameter(Mandatory)][string]$OwnershipToken,
    [Parameter(Mandatory)][string]$DatabaseMutationPath,
    [Parameter(Mandatory)][string]$DatabaseMutationToken,
    [Parameter(Mandatory)][string]$ExpectedVersion,
    [Parameter(Mandatory)][string]$HealthUrl,
    [Parameter(Mandatory)][string]$ServiceName,
    [Parameter(Mandatory)][string]$InstallRoot,
    [Parameter(Mandatory)][string]$UpdaterRoot,
    [Parameter(Mandatory)][string]$DataRoot
  )

  $helperBackup = Join-Path $UpdaterRoot ".update-helper-$TransactionId.backup"
  $guardianBackup = Join-Path $UpdaterRoot ".service-guardian-$TransactionId.backup"
  $helperStage = Join-Path $UpdaterRoot ".update-helper-$TransactionId.tmp"
  $guardianStage = Join-Path $UpdaterRoot ".service-guardian-$TransactionId.tmp"
  $databasePath = Join-Path $DataRoot 'data\treadmillrunner.db'
  $databaseSwap = "$databasePath.update-$TransactionId.replace-backup"
  $helperBackupSwap = "$helperBackup.replace-backup"
  $guardianBackupSwap = "$guardianBackup.replace-backup"
  $helperTargetSwap = "$HelperPath.update-$TransactionId.replace-backup"
  $guardianTargetSwap = "$GuardianPath.update-$TransactionId.replace-backup"
  $releaseRoot = Join-Path $InstallRoot 'releases'
  $planRoot = Join-Path $DataRoot 'updates\plans'
  $expectedPlanPath = Join-Path $planRoot 'pending-activation.json'
  $expectedNewReleasePath = Join-Path $releaseRoot $ExpectedVersion
  $expectedPreviousExecutable = $null
  $failure = $null
  $readySignaled = $false
  $ownershipAccepted = $false
  $transactionStarted = $false
  $terminalCleanupAllowed = $false
  $maintenanceMutex = $null
  $maintenanceMutexHeld = $false
  $databaseMutationObserved = $false
  $activationJournaled = $false
  try {
    $resolvedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
    $resolvedUpdaterRoot = Assert-ExactPath -Actual $UpdaterRoot -Expected (Join-Path $resolvedInstallRoot 'updater') -Name 'Updater root'
    $resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
    $resolvedReleaseRoot = Assert-ExactPath -Actual $releaseRoot -Expected (Join-Path $resolvedInstallRoot 'releases') -Name 'Release root'
    $resolvedPlanRoot = Assert-ExactPath -Actual $planRoot -Expected (Join-Path $resolvedDataRoot 'updates\plans') -Name 'Plan root'
    Assert-ExactPath -Actual $PlanPath -Expected $expectedPlanPath -Name 'Pending plan path' | Out-Null
    Assert-ExactPath -Actual $NewReleasePath -Expected $expectedNewReleasePath -Name 'New release path' | Out-Null
    $expectedIncomingPath = Join-Path $resolvedReleaseRoot ('.incoming-' + $TransactionId)
    Assert-ExactPath -Actual $IncomingPath -Expected $expectedIncomingPath -Name 'Incoming path' | Out-Null
    Assert-ExactPath -Actual $HelperPath -Expected (Join-Path $resolvedUpdaterRoot 'update-helper.ps1') -Name 'Protected helper path' | Out-Null
    Assert-ExactPath -Actual $GuardianPath -Expected (Join-Path $resolvedUpdaterRoot 'service-guardian.ps1') -Name 'Protected guardian path' | Out-Null
    Assert-ExactPath -Actual $helperBackup -Expected (Join-Path $resolvedUpdaterRoot ".update-helper-$TransactionId.backup") -Name 'Helper backup path' | Out-Null
    Assert-ExactPath -Actual $guardianBackup -Expected (Join-Path $resolvedUpdaterRoot ".service-guardian-$TransactionId.backup") -Name 'Guardian backup path' | Out-Null
    Assert-ExactPath -Actual $helperStage -Expected (Join-Path $resolvedUpdaterRoot ".update-helper-$TransactionId.tmp") -Name 'Helper stage path' | Out-Null
    Assert-ExactPath -Actual $guardianStage -Expected (Join-Path $resolvedUpdaterRoot ".service-guardian-$TransactionId.tmp") -Name 'Guardian stage path' | Out-Null
    Assert-ExactPath -Actual $PreconditionPath -Expected (Join-Path $resolvedUpdaterRoot ".update-preconditions-$TransactionId.json") -Name 'Precondition path' | Out-Null
    Assert-ExactPath -Actual $ReadyPath -Expected (Join-Path $resolvedUpdaterRoot ".update-ready-$TransactionId.token") -Name 'Ready path' | Out-Null
    Assert-ExactPath -Actual $StartPath -Expected (Join-Path $resolvedUpdaterRoot ".update-start-$TransactionId.token") -Name 'Start path' | Out-Null
    Assert-ExactPath -Actual $CompletionPath -Expected (Join-Path $resolvedUpdaterRoot ".update-completion-$TransactionId.token") -Name 'Completion path' | Out-Null
    Assert-ExactPath -Actual $OwnershipPath -Expected (Join-Path $resolvedUpdaterRoot ".update-ownership-$TransactionId.token") -Name 'Ownership path' | Out-Null
    Assert-ExactPath -Actual $DatabaseMutationPath -Expected (Join-Path $resolvedUpdaterRoot ".update-database-$TransactionId.token") -Name 'Database mutation path' | Out-Null
    Assert-ExactPath -Actual $DatabaseBackupPath -Expected (Join-Path $resolvedDataRoot "backups\pre-update-$TransactionId.db") -Name 'Database backup path' | Out-Null
    Assert-ExactPath -Actual $databaseSwap -Expected ((Join-Path $resolvedDataRoot 'data\treadmillrunner.db') + ".update-$TransactionId.replace-backup") -Name 'Database swap path' | Out-Null
    Assert-ExactPath -Actual $helperBackupSwap -Expected ((Join-Path $resolvedUpdaterRoot ".update-helper-$TransactionId.backup") + '.replace-backup') -Name 'Helper backup swap path' | Out-Null
    Assert-ExactPath -Actual $guardianBackupSwap -Expected ((Join-Path $resolvedUpdaterRoot ".service-guardian-$TransactionId.backup") + '.replace-backup') -Name 'Guardian backup swap path' | Out-Null
    Assert-ExactPath -Actual $helperTargetSwap -Expected ((Join-Path $resolvedUpdaterRoot 'update-helper.ps1') + ".update-$TransactionId.replace-backup") -Name 'Helper target swap path' | Out-Null
    Assert-ExactPath -Actual $guardianTargetSwap -Expected ((Join-Path $resolvedUpdaterRoot 'service-guardian.ps1') + ".update-$TransactionId.replace-backup") -Name 'Guardian target swap path' | Out-Null
    Assert-ExactPath -Actual $JournalPath -Expected (Join-Path $resolvedPlanRoot "transaction-$TransactionId.json") -Name 'Journal path' | Out-Null
    Assert-ExactPath -Actual $MaintenanceMarkerPath -Expected (Join-Path $resolvedDataRoot 'updates\service-maintenance.lock') -Name 'Maintenance marker path' | Out-Null
    $expectedPreviousExecutable = Get-ServiceExecutablePath -ImagePath $PreviousImagePath
    Assert-UnderRoot -Path $expectedPreviousExecutable -Root $resolvedReleaseRoot | Out-Null
    Assert-NoReparsePoint -Path $expectedPreviousExecutable -StopAt $resolvedInstallRoot
    if ([System.IO.Path]::GetFileName($expectedPreviousExecutable) -ne 'TreadmillRunner.Gateway.exe') { throw 'The previous service image is outside the release executable contract.' }
    if ([System.StringComparer]::OrdinalIgnoreCase.Equals($expectedPreviousExecutable, (Join-Path $expectedNewReleasePath 'TreadmillRunner.Gateway.exe'))) { throw 'The previous service image must differ from the promoted release.' }
    foreach ($path in @($HelperPath, $GuardianPath, $helperBackup, $guardianBackup, $helperStage, $guardianStage, $helperBackupSwap, $guardianBackupSwap, $helperTargetSwap, $guardianTargetSwap, $PreconditionPath, $ReadyPath, $StartPath, $CompletionPath, $OwnershipPath, $DatabaseMutationPath)) {
      Assert-NoReparsePoint -Path $path -StopAt $UpdaterRoot
    }
    Assert-NoReparsePoint -Path $IncomingPath -StopAt $resolvedReleaseRoot
    Assert-NoReparsePoint -Path $NewReleasePath -StopAt $resolvedInstallRoot
    foreach ($path in @($DatabasePath, $DatabaseBackupPath, $databaseSwap, $JournalPath, $MaintenanceMarkerPath)) {
      Assert-NoReparsePoint -Path $path -StopAt $resolvedDataRoot
    }
    if (-not (Test-Path -LiteralPath $PreconditionPath -PathType Leaf)) { throw 'Infrastructure refresh preconditions are missing.' }
    $preconditions = Get-Content -LiteralPath $PreconditionPath -Raw | ConvertFrom-Json
    $preconditionPreviousExecutable = Get-ServiceExecutablePath -ImagePath ([string]$preconditions.previousImagePath)
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($preconditionPreviousExecutable, $expectedPreviousExecutable)) {
      throw 'The infrastructure refresh previous service image changed before handoff.'
    }
    # The supervisor stores the service image in its canonical quoted form,
    # while this child receives a plain executable path. Normalize it once so
    # every rollback path preserves spaces in the Windows service ImagePath.
    $PreviousImagePath = '"{0}"' -f $expectedPreviousExecutable
    if (-not (Test-Path -LiteralPath $HelperPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $GuardianPath -PathType Leaf)) {
      throw 'The protected updater scripts are missing; infrastructure refresh is not safe.'
    }
    if ((Get-FileSha256 -Path $helperStage) -ne $ExpectedHelperHash -or
        (Get-FileSha256 -Path $guardianStage) -ne $ExpectedGuardianHash) {
      throw 'The verified incoming updater script hashes changed before infrastructure refresh.'
    }
    $previousHelperHash = Get-FileSha256 -Path $HelperPath
    $previousGuardianHash = Get-FileSha256 -Path $GuardianPath
    if ((Get-FileSha256 -Path (Join-Path $UpdaterRoot 'signing.cer')) -ne [string]$preconditions.certificateHash -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Actions.Execute -ne [string]$preconditions.updateExecute -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Actions.Arguments -ne [string]$preconditions.updateArguments -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Principal.UserId -ne [string]$preconditions.updatePrincipalUserId -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Principal.LogonType -ne [string]$preconditions.updatePrincipalLogonType -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Principal.RunLevel -ne [string]$preconditions.updatePrincipalRunLevel -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Actions.Execute -ne [string]$preconditions.guardianExecute -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Actions.Arguments -ne [string]$preconditions.guardianArguments -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Principal.UserId -ne [string]$preconditions.guardianPrincipalUserId -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Principal.LogonType -ne [string]$preconditions.guardianPrincipalLogonType -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Principal.RunLevel -ne [string]$preconditions.guardianPrincipalRunLevel) {
      throw 'The pinned certificate or protected task contract changed before handoff.'
    }
    Write-RefreshReady -Path $ReadyPath -Token $ReadyToken
    $readySignaled = $true
    # The parent remains responsible until this child claims the shared mutex.
    $maintenanceMutex = New-MaintenanceMutex
    if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) { throw 'The update maintenance lock could not be acquired after handoff.' }
    $maintenanceMutexHeld = $true
    if ((Get-FileSha256 -Path (Join-Path $UpdaterRoot 'signing.cer')) -ne [string]$preconditions.certificateHash -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Actions.Execute -ne [string]$preconditions.updateExecute -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Actions.Arguments -ne [string]$preconditions.updateArguments -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Principal.UserId -ne [string]$preconditions.updatePrincipalUserId -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Principal.LogonType -ne [string]$preconditions.updatePrincipalLogonType -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop).Principal.RunLevel -ne [string]$preconditions.updatePrincipalRunLevel -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Actions.Execute -ne [string]$preconditions.guardianExecute -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Actions.Arguments -ne [string]$preconditions.guardianArguments -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Principal.UserId -ne [string]$preconditions.guardianPrincipalUserId -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Principal.LogonType -ne [string]$preconditions.guardianPrincipalLogonType -or
        [string](Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop).Principal.RunLevel -ne [string]$preconditions.guardianPrincipalRunLevel) {
      throw 'The pinned certificate or protected task actions changed during activation.'
    }
    $parentProcess = Get-Process -Id ([int]$preconditions.parentProcessId) -ErrorAction SilentlyContinue
    if ($null -eq $parentProcess -or
        $parentProcess.StartTime.ToUniversalTime().Ticks -ne [long]$preconditions.parentProcessStartTicks) {
      throw 'The updater supervisor exited before the child claimed transaction ownership.'
    }
    Write-RefreshReady -Path $OwnershipPath -Token $OwnershipToken
    $ownershipAccepted = $true
    $maintenanceMutex.ReleaseMutex()
    $maintenanceMutexHeld = $false
    Wait-RefreshStartSignal -ParentProcessId $ParentProcessId -Path $StartPath -Token $StartToken -TimeoutSeconds 180 | Out-Null
    $transactionStarted = $true
    $maintenanceMutex = New-MaintenanceMutex
    if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) { throw 'The update maintenance lock could not be reacquired after transaction start.' }
    $maintenanceMutexHeld = $true
    if (Test-Path -LiteralPath $DatabaseMutationPath -PathType Leaf) {
      $databaseMutationObserved = [System.StringComparer]::Ordinal.Equals((Get-Content -LiteralPath $DatabaseMutationPath -Raw).Trim(), $DatabaseMutationToken)
      if (-not $databaseMutationObserved) { throw 'The updater supervisor produced an invalid database-mutation token.' }
    }
    if ((Get-FileSha256 -Path (Join-Path $NewReleasePath 'Updates\update-helper.ps1')) -ne $ExpectedHelperHash -or
        (Get-FileSha256 -Path (Join-Path $NewReleasePath 'Updates\service-guardian.ps1')) -ne $ExpectedGuardianHash) {
      throw 'The verified incoming updater script hashes changed before infrastructure refresh.'
    }
    Copy-DurableFile -Source $HelperPath -Destination $helperBackup -ReplacementBackupPath $helperBackupSwap
    Copy-DurableFile -Source $GuardianPath -Destination $guardianBackup -ReplacementBackupPath $guardianBackupSwap
    Copy-DurableFile -Source $helperStage -Destination $HelperPath -ReplacementBackupPath $helperTargetSwap
    Copy-DurableFile -Source $guardianStage -Destination $GuardianPath -ReplacementBackupPath $guardianTargetSwap
    if ((Get-FileSha256 -Path $HelperPath) -ne $ExpectedHelperHash -or
        (Get-FileSha256 -Path $GuardianPath) -ne $ExpectedGuardianHash) {
      throw 'The protected updater scripts could not be verified after replacement.'
    }

    Start-Service -Name $ServiceName -ErrorAction Stop
    if (-not (Wait-ReleaseHealth -HealthUrl $HealthUrl -ExpectedVersion $ExpectedVersion)) {
      throw 'The promoted release did not report the expected version and readiness within 120 seconds.'
    }
    Write-JournalPayload -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion -State 'Activated' -Reason 'Expected version, readiness, and protected updater hashes were confirmed.'
    $activationJournaled = $true
    Wait-RefreshCompletionAcknowledgement -Path $CompletionPath -Token $CompletionToken -TimeoutSeconds 180 | Out-Null
    $terminalCleanupAllowed = $true
  }
  catch {
    $failure = $_.Exception.Message
    if ($activationJournaled) {
      # Activated is written only after the promoted release, readiness, and
      # protected hashes are verified. Losing the parent acknowledgement after
      # that durable terminal record must never turn success into rollback.
      $terminalCleanupAllowed = $true
      return
    }
    if (-not $ownershipAccepted) {
      # The parent has not transferred responsibility yet. Let its guarded
      # rollback restore the promoted release and terminate this child.
      throw
    }
    if (-not $transactionStarted) {
      try {
        # Before the parent signals transaction start, the child owns only the
        # staged incoming workspace. Do not stop the service, restore the
        # database, rewrite the service image, or touch a release directory.
        if (Test-Path -LiteralPath $IncomingPath) {
          Remove-Item -LiteralPath $IncomingPath -Recurse -Force
        }
        if (Test-Path -LiteralPath $IncomingPath) { throw 'The transaction-owned incoming workspace could not be removed.' }
        Write-JournalPayload -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion -State 'RolledBack' -Reason "$failure before transaction start; removed only the transaction-owned incoming workspace."
        $terminalCleanupAllowed = $true
      }
      catch {
        Write-JournalPayload -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion -State 'RollbackFailed' -Reason ("$failure Rollback: $($_.Exception.Message)")
        throw
      }
      throw
    }
    if (-not $maintenanceMutexHeld) {
      try {
        $maintenanceMutex = New-MaintenanceMutex
        if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) { throw 'The updater child could not reacquire the maintenance lock for rollback.' }
        $maintenanceMutexHeld = $true
      }
      catch {
        Write-JournalPayload -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion -State 'RollbackFailed' -Reason "$failure The maintenance lock was not acquired; recovery artifacts were preserved."
        throw
      }
    }
    try {
      Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
      $databaseBackupHash = Get-FileSha256 -Path $DatabaseBackupPath
      Reconcile-TransactionSwap -Destination $databasePath -ReplacementBackupPath $databaseSwap -DurableSourcePath $DatabaseBackupPath -ExpectedHash $databaseBackupHash
      Reconcile-TransactionSwap -Destination $helperBackup -ReplacementBackupPath $helperBackupSwap -DurableSourcePath $HelperPath -ExpectedHash $previousHelperHash
      Reconcile-TransactionSwap -Destination $guardianBackup -ReplacementBackupPath $guardianBackupSwap -DurableSourcePath $GuardianPath -ExpectedHash $previousGuardianHash
      Reconcile-TransactionSwap -Destination $HelperPath -ReplacementBackupPath $helperTargetSwap -DurableSourcePath $helperBackup -ExpectedHash $previousHelperHash
      Reconcile-TransactionSwap -Destination $GuardianPath -ReplacementBackupPath $guardianTargetSwap -DurableSourcePath $guardianBackup -ExpectedHash $previousGuardianHash
      if ($databaseMutationObserved) {
        Remove-Item -LiteralPath ($DataRoot + '\data\treadmillrunner.db-wal') -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ($DataRoot + '\data\treadmillrunner.db-shm') -Force -ErrorAction SilentlyContinue
        Copy-DurableFile -Source $DatabaseBackupPath -Destination $databasePath -ReplacementBackupPath $databaseSwap
        if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf) -or
            (Get-FileSha256 -Path $databasePath) -ne $databaseBackupHash) {
          throw 'The database backup was not restored before rollback cleanup.'
        }
      }
      if (Test-Path -LiteralPath $helperBackup -PathType Leaf) { Copy-DurableFile -Source $helperBackup -Destination $HelperPath -ReplacementBackupPath $helperTargetSwap }
      if (Test-Path -LiteralPath $guardianBackup -PathType Leaf) { Copy-DurableFile -Source $guardianBackup -Destination $GuardianPath -ReplacementBackupPath $guardianTargetSwap }
      if (-not (Test-Path -LiteralPath $HelperPath -PathType Leaf) -or
          -not (Test-Path -LiteralPath $GuardianPath -PathType Leaf) -or
          (Get-FileSha256 -Path $HelperPath) -ne $previousHelperHash -or
          (Get-FileSha256 -Path $GuardianPath) -ne $previousGuardianHash) {
        throw 'The protected updater scripts were not restored before rollback cleanup.'
      }
      Restore-ServiceImageSafely -ServiceName $ServiceName `
        -TargetImagePath (Join-Path $NewReleasePath 'TreadmillRunner.Gateway.exe') `
        -PreviousImagePath $PreviousImagePath
      Start-Service -Name $ServiceName -ErrorAction Stop
      if (-not (Wait-ReleaseHealth -HealthUrl $HealthUrl -ExpectedVersion '')) {
        throw 'The previous release did not recover readiness after infrastructure refresh rollback.'
      }
      Assert-ServiceUsesImagePath -ServiceName $ServiceName -ExpectedImagePath $PreviousImagePath
      Remove-FailedRelease -NewReleasePath $NewReleasePath -ReleaseRoot $resolvedReleaseRoot -PreviousImagePath $PreviousImagePath
      Write-JournalPayload -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion -State 'RolledBack' -Reason $failure
      $terminalCleanupAllowed = $true
    }
    catch {
      Write-JournalPayload -Path $JournalPath -TransactionId $TransactionId -Version $ExpectedVersion -State 'RollbackFailed' -Reason ("$failure Rollback: $($_.Exception.Message)")
      throw
    }
    throw
  }
  finally {
    if ($maintenanceMutexHeld) {
      $maintenanceMutex.ReleaseMutex()
      $maintenanceMutex.Dispose()
    }
    if ($terminalCleanupAllowed) {
      # Terminal activation is already journaled and acknowledged. These
      # deletions are hygiene only; a locked or ACL-protected artifact must
      # not change the terminal outcome or trigger rollback.
      Remove-Item -LiteralPath $ReadyPath -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $StartPath -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $CompletionPath -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $OwnershipPath -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $DatabaseMutationPath -Force -ErrorAction SilentlyContinue
      foreach ($path in @($helperBackup, $guardianBackup)) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
      }
      foreach ($path in @($databaseSwap, $helperBackupSwap, $guardianBackupSwap, $helperTargetSwap, $guardianTargetSwap)) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
      }
      foreach ($path in @($ReadyPath, $StartPath, $CompletionPath, $OwnershipPath, $DatabaseMutationPath, $JournalPath, $PreconditionPath)) {
        Remove-Item -LiteralPath ("$path.replace-backup") -Force -ErrorAction SilentlyContinue
      }
      Remove-Item -LiteralPath (Join-Path $UpdaterRoot ('.update-helper-' + $TransactionId + '.tmp')) -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath (Join-Path $UpdaterRoot ('.service-guardian-' + $TransactionId + '.tmp')) -Force -ErrorAction SilentlyContinue
    }
    if ($ownershipAccepted -and $terminalCleanupAllowed) {
      try {
        Remove-OwnedMaintenanceMarker -Path $MaintenanceMarkerPath -TransactionId $TransactionId
        Remove-Item -LiteralPath $PlanPath -Force -ErrorAction Stop
        Remove-Item -LiteralPath $PreconditionPath -Force -ErrorAction SilentlyContinue
      }
      catch { }
    }
  }
}

if ($InfrastructureRefresh) {
  $refreshUpdaterRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
  if ([string]::IsNullOrWhiteSpace($RefreshTransactionId) -or $RefreshTransactionId -notmatch '^[0-9a-f]{32}$') { throw 'The infrastructure refresh transaction identifier is invalid.' }
  if ([string]::IsNullOrWhiteSpace($RefreshExpectedVersion) -or $RefreshExpectedVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'The infrastructure refresh version is invalid.' }
  if ([string]::IsNullOrWhiteSpace($RefreshReadyToken) -or [string]::IsNullOrWhiteSpace($RefreshStartToken) -or [string]::IsNullOrWhiteSpace($RefreshCompletionToken) -or [string]::IsNullOrWhiteSpace($RefreshOwnershipToken) -or [string]::IsNullOrWhiteSpace($RefreshDatabaseMutationToken)) { throw 'The infrastructure refresh handoff token is missing.' }
  Invoke-InfrastructureRefresh `
    -PlanPath ([System.IO.Path]::GetFullPath($PlanPath)) `
    -TransactionId $RefreshTransactionId `
    -NewReleasePath ([System.IO.Path]::GetFullPath($RefreshNewReleasePath)) `
    -IncomingPath ([System.IO.Path]::GetFullPath($RefreshIncomingPath)) `
    -PreviousImagePath $RefreshPreviousImagePath `
    -DatabaseBackupPath $RefreshDatabaseBackupPath `
    -JournalPath $RefreshJournalPath `
    -MaintenanceMarkerPath $RefreshMaintenanceMarkerPath `
    -HelperPath $RefreshHelperPath `
    -GuardianPath $RefreshGuardianPath `
    -ReadyPath ([System.IO.Path]::GetFullPath($RefreshReadyPath)) `
    -ReadyToken $RefreshReadyToken `
    -StartPath ([System.IO.Path]::GetFullPath($RefreshStartPath)) `
    -StartToken $RefreshStartToken `
    -CompletionPath ([System.IO.Path]::GetFullPath($RefreshCompletionPath)) `
    -CompletionToken $RefreshCompletionToken `
    -OwnershipPath ([System.IO.Path]::GetFullPath($RefreshOwnershipPath)) `
    -OwnershipToken $RefreshOwnershipToken `
    -DatabaseMutationPath ([System.IO.Path]::GetFullPath($RefreshDatabaseMutationPath)) `
    -DatabaseMutationToken $RefreshDatabaseMutationToken `
    -ExpectedHelperHash $RefreshExpectedHelperHash `
    -ExpectedGuardianHash $RefreshExpectedGuardianHash `
    -PreconditionPath $RefreshPreconditionPath `
    -ExpectedVersion $RefreshExpectedVersion `
    -HealthUrl $HealthUrl `
    -ServiceName 'TreadmillRunnerGateway' `
    -InstallRoot ([System.IO.Path]::GetFullPath($InstallRoot)) `
    -UpdaterRoot $refreshUpdaterRoot `
    -DataRoot ([System.IO.Path]::GetFullPath($DataRoot))
  exit 0
}

Start-Sleep -Seconds 2
$resolvedPlan = [System.IO.Path]::GetFullPath($PlanPath)
$installRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$dataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$updaterRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$helperTarget = Join-Path $updaterRoot 'update-helper.ps1'
$guardianTarget = Join-Path $updaterRoot 'service-guardian.ps1'
if ($updaterRoot -ne [System.IO.Path]::GetFullPath((Join-Path $installRoot 'updater'))) {
  throw 'The privileged updater is not running from its administrator-owned install location.'
}
$planRoot = Join-Path $dataRoot 'updates\plans'
$expectedPlan = [System.IO.Path]::GetFullPath((Join-Path $planRoot 'pending-activation.json'))
if ($resolvedPlan -ne $expectedPlan) { throw 'The privileged updater accepts only its fixed pending-plan inbox.' }
if (-not (Test-Path -LiteralPath $resolvedPlan -PathType Leaf)) { throw 'The pending activation plan is missing.' }
$plan = Get-Content -LiteralPath $resolvedPlan -Raw | ConvertFrom-Json

$transactionId = [string]$plan.TransactionId
$version = [string]$plan.Version
if ($transactionId -notmatch '^[0-9a-f]{32}$') { throw 'The update transaction identifier is invalid.' }
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'The update version is invalid.' }

$serviceName = 'TreadmillRunnerGateway'
$releaseRoot = Join-Path $installRoot 'releases'
$stagingRoot = Join-Path $dataRoot 'updates\staging'
$stagePath = Assert-UnderRoot -Path (Join-Path $stagingRoot $version) -Root $stagingRoot
$manifestPath = Assert-UnderRoot -Path (Join-Path $stagePath 'verified-manifest.json') -Root $stagePath
$certificatePath = Assert-UnderRoot -Path (Join-Path $updaterRoot 'signing.cer') -Root $installRoot
$databasePath = Assert-UnderRoot -Path (Join-Path $dataRoot 'data\treadmillrunner.db') -Root $dataRoot
$databaseBackupPath = Assert-UnderRoot -Path (Join-Path $dataRoot "backups\pre-update-$transactionId.db") -Root $dataRoot
$journalPath = Assert-UnderRoot -Path (Join-Path $planRoot "transaction-$transactionId.json") -Root $dataRoot
$maintenanceMarkerPath = Assert-UnderRoot -Path (Join-Path $dataRoot 'updates\service-maintenance.lock') -Root $dataRoot
$healthUri = [Uri]$HealthUrl

foreach ($path in @($releaseRoot, $stagingRoot, $stagePath, $manifestPath, $certificatePath, $databasePath, $databaseBackupPath, $journalPath, $maintenanceMarkerPath)) {
  Assert-NoReparsePoint -Path $path -StopAt $(if ($path.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $installRoot } else { $dataRoot })
}

if (-not $healthUri.IsLoopback -or $healthUri.Scheme -ne 'http') { throw 'The update health URL must be loopback HTTP.' }
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $certificatePath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $databaseBackupPath -PathType Leaf)) {
  throw 'The staged manifest, pinned public certificate, or database backup is missing.'
}

$manifestText = [System.IO.File]::ReadAllText($manifestPath, [System.Text.Encoding]::UTF8)
$manifest = $manifestText | ConvertFrom-Json
if ([int]$manifest.schemaVersion -ne 1 -or [string]$manifest.channel -ne 'stable' -or
    [string]$manifest.version -ne $version -or
    [string]$manifest.packageSha256 -notmatch '^[0-9A-Fa-f]{64}$' -or
    [int]$manifest.minimumDatabaseSchemaVersion -lt 0 -or
    [int]$manifest.maximumDatabaseSchemaVersion -lt [int]$manifest.minimumDatabaseSchemaVersion) {
  throw 'The staged manifest does not match the activation plan.'
}
$packageName = [System.IO.Path]::GetFileName([string]$manifest.packageFileName)
if ($packageName -ne [string]$manifest.packageFileName -or -not $packageName.EndsWith('.zip', [StringComparison]::OrdinalIgnoreCase)) {
  throw 'The staged package name is invalid.'
}
$packagePath = Assert-UnderRoot -Path (Join-Path $stagePath $packageName) -Root $stagePath
Assert-NoReparsePoint -Path $packagePath -StopAt $dataRoot
if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) { throw 'The signed package is missing.' }
if ((Get-Item -LiteralPath $packagePath).Length -gt 1GB) { throw 'The signed package is too large.' }
$notes = ([string]$manifest.releaseNotes).Replace("`r`n", "`n").Replace("`r", "`n")
$payload = @(
  [string]$manifest.schemaVersion,
  [string]$manifest.version,
  [string]$manifest.channel,
  [string]$manifest.packageFileName,
  ([string]$manifest.packageSha256).ToUpperInvariant(),
  [string]$manifest.minimumDatabaseSchemaVersion,
  [string]$manifest.maximumDatabaseSchemaVersion,
  $notes
) -join "`n"
$certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($certificatePath)
$rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey($certificate)
try {
  $signature = [Convert]::FromBase64String([string]$manifest.signature)
  $validSignature = $rsa.VerifyData(
    [System.Text.Encoding]::UTF8.GetBytes($payload),
    $signature,
    [System.Security.Cryptography.HashAlgorithmName]::SHA256,
    [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
  if (-not $validSignature) { throw 'The staged manifest signature is invalid.' }
}
finally {
  if ($null -ne $rsa) { $rsa.Dispose() }
  $certificate.Dispose()
}
$actualHash = Get-FileSha256 -Path $packagePath
if ($actualHash -ne ([string]$manifest.packageSha256).ToUpperInvariant()) {
  throw 'The staged package hash changed after verification.'
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$newReleasePath = Assert-UnderRoot -Path (Join-Path $releaseRoot $version) -Root $releaseRoot
$incomingPath = Assert-UnderRoot -Path (Join-Path $releaseRoot ('.incoming-' + $transactionId)) -Root $releaseRoot
$preconditionPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-preconditions-' + $transactionId + '.json')) -Root $updaterRoot
$helperBackupPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-helper-' + $transactionId + '.backup')) -Root $updaterRoot
$guardianBackupPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.service-guardian-' + $transactionId + '.backup')) -Root $updaterRoot
$databaseSwapPath = "$databasePath.update-$transactionId.replace-backup"
$helperBackupSwapPath = "$helperBackupPath.replace-backup"
$guardianBackupSwapPath = "$guardianBackupPath.replace-backup"
$helperTargetSwapPath = "$helperTarget.update-$transactionId.replace-backup"
$guardianTargetSwapPath = "$guardianTarget.update-$transactionId.replace-backup"
$incomingCreated = $false
$previousImagePath = $null
$maintenanceMarkerCreated = $false
$refreshHandoffStarted = $false
$refreshChildProcess = $null
$refreshChildRolledBack = $false
$refreshChildTerminalState = $null
$activationCompleted = $false
$refreshReadyPath = $null
$refreshReadyToken = $null
$refreshStartPath = $null
$refreshStartToken = $null
$refreshCompletionPath = $null
$refreshCompletionToken = $null
$refreshOwnershipPath = $null
$refreshOwnershipToken = $null
$databaseMutationPath = $null
$databaseMutationToken = $null
$newReleasePromoted = $false
$previousHelperHash = $null
$previousGuardianHash = $null
$rollbackCompleted = $false
$databaseMutationStarted = $false
$serviceMutationStarted = $false
$maintenanceMutex = $null
$maintenanceMutexHeld = $false
if (Invoke-StaleUpdateRecovery `
    -TransactionId $transactionId -ExpectedVersion $version -PlanPath $resolvedPlan `
    -JournalPath $journalPath -MaintenanceMarkerPath $maintenanceMarkerPath `
    -PreconditionPath $preconditionPath -IncomingPath $incomingPath -NewReleasePath $newReleasePath `
    -DatabasePath $databasePath -DatabaseBackupPath $databaseBackupPath `
    -HelperPath $helperTarget -GuardianPath $guardianTarget -UpdaterRoot $updaterRoot `
    -ReleaseRoot $releaseRoot -HealthUrl $healthUri.AbsoluteUri -ServiceName $serviceName) {
  exit 0
}
try {
  $maintenanceMutex = New-MaintenanceMutex
  if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) { throw 'The update maintenance lock could not be acquired.' }
  $maintenanceMutexHeld = $true
  if (Test-Path -LiteralPath $newReleasePath) { throw 'The immutable target release already exists.' }
  # With no marker or journal, these exact transaction-id paths can only be
  # preparation artifacts left before the first risky mutation. The mutex has
  # been held since before cleanup and remains held through child handoff.
  foreach ($orphan in @(
    $incomingPath, $preconditionPath, $helperBackupPath, $guardianBackupPath,
    $databaseSwapPath, $helperBackupSwapPath, $guardianBackupSwapPath,
    $helperTargetSwapPath, $guardianTargetSwapPath,
    ("$resolvedPlan.replace-backup"), ("$preconditionPath.replace-backup"),
    ("$(Join-Path $updaterRoot ".update-ready-$transactionId.token").replace-backup"),
    ("$(Join-Path $updaterRoot ".update-start-$transactionId.token").replace-backup"),
    ("$(Join-Path $updaterRoot ".update-completion-$transactionId.token").replace-backup"),
    ("$(Join-Path $updaterRoot ".update-ownership-$transactionId.token").replace-backup"),
    ("$(Join-Path $updaterRoot ".update-database-$transactionId.token").replace-backup"),
    (Join-Path $updaterRoot ".update-helper-$transactionId.tmp"),
    (Join-Path $updaterRoot ".service-guardian-$transactionId.tmp"),
    (Join-Path $updaterRoot ".update-ready-$transactionId.token"),
    (Join-Path $updaterRoot ".update-start-$transactionId.token"),
    (Join-Path $updaterRoot ".update-completion-$transactionId.token"),
    (Join-Path $updaterRoot ".update-ownership-$transactionId.token"),
    (Join-Path $updaterRoot ".update-database-$transactionId.token")
  )) {
    Assert-NoReparsePoint -Path $orphan -StopAt $(if ($orphan.StartsWith($releaseRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $releaseRoot } elseif ($orphan.StartsWith($updaterRoot, [System.StringComparison]::OrdinalIgnoreCase)) { $updaterRoot } else { $dataRoot })
    if (Test-Path -LiteralPath $orphan -PathType Container) { Remove-Item -LiteralPath $orphan -Recurse -Force }
    else { Remove-Item -LiteralPath $orphan -Force -ErrorAction SilentlyContinue }
  }
  New-Item -ItemType Directory -Path $incomingPath | Out-Null
  $incomingCreated = $true
  $archive = [System.IO.Compression.ZipFile]::OpenRead($packagePath)
  try {
    if ($archive.Entries.Count -gt 10000) { throw 'The signed package contains too many entries.' }
    $paths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $expandedBytes = [long]0
    foreach ($entry in $archive.Entries) {
      $entryPath = $entry.FullName.Replace('\', '/')
      if ([string]::IsNullOrWhiteSpace($entryPath) -or $entryPath.StartsWith('/') -or $entryPath.Contains(':') -or
          ($entryPath.Split('/') | Where-Object { $_ -eq '..' -or $_ -eq '.' }) -or -not $paths.Add($entryPath)) {
        throw 'The signed package contains an unsafe archive path.'
      }
      if ([long]$entry.Length -gt (2GB - $expandedBytes)) { throw 'The expanded signed package is too large.' }
      $expandedBytes += [long]$entry.Length
      $destination = [System.IO.Path]::GetFullPath((Join-Path $incomingPath $entryPath))
      [void](Assert-UnderRoot -Path $destination -Root $incomingPath)
    }
    foreach ($requiredEntry in @('TreadmillRunner.Gateway.exe', 'TreadmillRunner.Migrations.exe', 'Updates/update-helper.ps1', 'Updates/service-guardian.ps1')) {
      if (-not $paths.Contains($requiredEntry)) { throw "The signed package is missing $requiredEntry." }
    }
  }
  finally { $archive.Dispose() }
  [System.IO.Compression.ZipFile]::ExtractToDirectory($packagePath, $incomingPath)
  Assert-NoReparsePoint -Path $incomingPath -StopAt $releaseRoot

  $newExecutable = Join-Path $incomingPath 'TreadmillRunner.Gateway.exe'
  if (-not (Test-Path -LiteralPath $newExecutable -PathType Leaf)) { throw 'The release executable is missing.' }
  $migrationBundle = Join-Path $incomingPath 'TreadmillRunner.Migrations.exe'

  $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
  if ($null -eq $service) { throw 'The gateway service is not installed.' }
  $previousImagePath = [string]$service.PathName
  # The protected rollback contract restores one exact executable image. Any
  # service arguments would have to be preserved and revalidated separately,
  # so reject them before stopping the service or touching the database.
  $currentExecutable = Get-ServiceExecutablePath -ImagePath $previousImagePath
  if (-not (Test-Path -LiteralPath $currentExecutable -PathType Leaf)) {
    throw 'The installed service binary path is not a single existing executable path.'
  }
  $currentExecutable = Assert-UnderRoot -Path $currentExecutable -Root $releaseRoot
  if ([System.IO.Path]::GetFileName($currentExecutable) -ne 'TreadmillRunner.Gateway.exe') {
    throw 'The installed service executable is outside the immutable release contract.'
  }
  $previousImagePath = '"{0}"' -f $currentExecutable
  $currentReleasePath = Split-Path -Parent $currentExecutable
  $currentVersionText = Split-Path -Leaf $currentReleasePath
  if ($currentVersionText -notmatch '^\d+\.\d+\.\d+$') {
    throw 'The installed release directory has an invalid version.'
  }
  $currentVersion = [Version]$currentVersionText
  if ([Version]$version -le $currentVersion) { throw 'The signed release is not newer than the installed release.' }
  $preconditionPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-preconditions-' + $transactionId + '.json')) -Root $updaterRoot
  $helperRefreshPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-helper-' + $transactionId + '.tmp')) -Root $updaterRoot
  $guardianRefreshPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.service-guardian-' + $transactionId + '.tmp')) -Root $updaterRoot
  $helperBackupPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-helper-' + $transactionId + '.backup')) -Root $updaterRoot
  $guardianBackupPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.service-guardian-' + $transactionId + '.backup')) -Root $updaterRoot
  $refreshReadyPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-ready-' + $transactionId + '.token')) -Root $updaterRoot
  $refreshStartPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-start-' + $transactionId + '.token')) -Root $updaterRoot
  $refreshCompletionPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-completion-' + $transactionId + '.token')) -Root $updaterRoot
  $refreshOwnershipPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-ownership-' + $transactionId + '.token')) -Root $updaterRoot
  $databaseMutationPath = Assert-UnderRoot -Path (Join-Path $updaterRoot ('.update-database-' + $transactionId + '.token')) -Root $updaterRoot
  foreach ($path in @($preconditionPath, $helperRefreshPath, $guardianRefreshPath, $refreshReadyPath, $refreshStartPath, $refreshCompletionPath, $refreshOwnershipPath, $databaseMutationPath)) {
    if (Test-Path -LiteralPath $path) { throw 'An infrastructure refresh workspace already exists.' }
    Assert-NoReparsePoint -Path $path -StopAt $updaterRoot
  }
  if (-not (Test-Path -LiteralPath $helperTarget -PathType Leaf) -or
      -not (Test-Path -LiteralPath $guardianTarget -PathType Leaf)) {
      throw 'The protected updater scripts are missing; signed activation cannot refresh infrastructure safely.'
  }
  $previousHelperHash = Get-FileSha256 -Path $helperTarget
  $previousGuardianHash = Get-FileSha256 -Path $guardianTarget
  $refreshReadyToken = [Guid]::NewGuid().ToString('N')
  $refreshStartToken = [Guid]::NewGuid().ToString('N')
  $refreshCompletionToken = [Guid]::NewGuid().ToString('N')
  $refreshOwnershipToken = [Guid]::NewGuid().ToString('N')
  $databaseMutationToken = [Guid]::NewGuid().ToString('N')
  $updateTask = Get-ScheduledTask -TaskName 'TreadmillRunnerUpdate' -ErrorAction Stop
  $guardianTask = Get-ScheduledTask -TaskName 'TreadmillRunnerGuardian' -ErrorAction Stop
  $certificateHash = Get-FileSha256 -Path $certificatePath
  $parentProcess = Get-Process -Id $PID -ErrorAction Stop
  $helperExpectedHash = Get-FileSha256 -Path (Join-Path $incomingPath 'Updates\update-helper.ps1')
  $guardianExpectedHash = Get-FileSha256 -Path (Join-Path $incomingPath 'Updates\service-guardian.ps1')
  Write-DurableTextFile -Path $preconditionPath -Content ([ordered]@{
      schemaVersion = 1
      transactionId = $transactionId
      version = $version
      previousImagePath = $previousImagePath
      previousHelperHash = $previousHelperHash
      previousGuardianHash = $previousGuardianHash
      expectedHelperHash = $helperExpectedHash
      expectedGuardianHash = $guardianExpectedHash
      startToken = $refreshStartToken
      databaseMutationToken = $databaseMutationToken
      parentProcessId = $PID
      parentProcessStartTicks = $parentProcess.StartTime.ToUniversalTime().Ticks
      certificateHash = $certificateHash
      updateExecute = [string]$updateTask.Actions.Execute
      updateArguments = [string]$updateTask.Actions.Arguments
      updatePrincipalUserId = [string]$updateTask.Principal.UserId
      updatePrincipalLogonType = [string]$updateTask.Principal.LogonType
      updatePrincipalRunLevel = [string]$updateTask.Principal.RunLevel
      guardianExecute = [string]$guardianTask.Actions.Execute
      guardianArguments = [string]$guardianTask.Actions.Arguments
      guardianPrincipalUserId = [string]$guardianTask.Principal.UserId
      guardianPrincipalLogonType = [string]$guardianTask.Principal.LogonType
      guardianPrincipalRunLevel = [string]$guardianTask.Principal.RunLevel
    } | ConvertTo-Json -Compress)
  Copy-DurableFile -Source (Join-Path $incomingPath 'Updates\update-helper.ps1') -Destination $helperRefreshPath
  Copy-DurableFile -Source (Join-Path $incomingPath 'Updates\service-guardian.ps1') -Destination $guardianRefreshPath
  if ((Get-FileSha256 -Path $helperRefreshPath) -ne $helperExpectedHash -or
      (Get-FileSha256 -Path $guardianRefreshPath) -ne $guardianExpectedHash) {
    throw 'The staged protected updater scripts could not be hash verified.'
  }
  $refreshArguments = @(
    '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $helperRefreshPath),
    '-PlanPath', ('"{0}"' -f $resolvedPlan), '-InstallRoot', ('"{0}"' -f $installRoot),
    '-DataRoot', ('"{0}"' -f $dataRoot), '-HealthUrl', ('"{0}"' -f $healthUri.AbsoluteUri),
    '-InfrastructureRefresh', '-ParentProcessId', [string]$PID,
    '-RefreshTransactionId', $transactionId, '-RefreshExpectedVersion', $version,
    '-RefreshNewReleasePath', ('"{0}"' -f $newReleasePath),
    '-RefreshIncomingPath', ('"{0}"' -f $incomingPath),
    # $previousImagePath is already quoted for sc.exe. Pass the raw
    # executable here and add exactly one argument-boundary quote pair; a
    # second quote layer is parsed by Windows PowerShell as a truncated path.
    '-RefreshPreviousImagePath', ('"{0}"' -f $currentExecutable),
    '-RefreshDatabaseBackupPath', ('"{0}"' -f $databaseBackupPath),
    '-RefreshJournalPath', ('"{0}"' -f $journalPath),
    '-RefreshMaintenanceMarkerPath', ('"{0}"' -f $maintenanceMarkerPath),
    '-RefreshHelperPath', ('"{0}"' -f $helperTarget),
    '-RefreshGuardianPath', ('"{0}"' -f $guardianTarget),
    '-RefreshReadyPath', ('"{0}"' -f $refreshReadyPath),
    '-RefreshReadyToken', $refreshReadyToken,
    '-RefreshStartPath', ('"{0}"' -f $refreshStartPath),
    '-RefreshStartToken', $refreshStartToken,
    '-RefreshCompletionPath', ('"{0}"' -f $refreshCompletionPath),
    '-RefreshCompletionToken', $refreshCompletionToken,
    '-RefreshOwnershipPath', ('"{0}"' -f $refreshOwnershipPath),
    '-RefreshOwnershipToken', $refreshOwnershipToken,
    '-RefreshDatabaseMutationPath', ('"{0}"' -f $databaseMutationPath),
    '-RefreshDatabaseMutationToken', $databaseMutationToken,
    '-RefreshExpectedHelperHash', $helperExpectedHash,
    '-RefreshExpectedGuardianHash', $guardianExpectedHash,
    '-RefreshPreconditionPath', ('"{0}"' -f $preconditionPath)
  )
  $refreshChildProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList $refreshArguments -WindowStyle Hidden -PassThru
  if ($null -eq $refreshChildProcess) { throw 'The protected updater refresh process could not be started.' }
  Wait-RefreshChildReady -Process $refreshChildProcess -Path $refreshReadyPath -Token $refreshReadyToken -TimeoutSeconds 30 | Out-Null
  if (Test-Path -LiteralPath $maintenanceMarkerPath) { throw 'A maintenance marker already exists.' }
  $markerStream = [System.IO.File]::Open($maintenanceMarkerPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
  try {
    $parentProcess = Get-Process -Id $PID -ErrorAction Stop
    $refreshChildProcess.Refresh()
    $markerBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
      "update $transactionId parent=$PID parentStart=$($parentProcess.StartTime.ToUniversalTime().Ticks) child=$($refreshChildProcess.Id) childStart=$($refreshChildProcess.StartTime.ToUniversalTime().Ticks) $([DateTimeOffset]::UtcNow.ToString('O'))")
    $markerStream.Write($markerBytes, 0, $markerBytes.Length)
    $markerStream.Flush($true)
    $maintenanceMarkerCreated = $true
  }
  finally { $markerStream.Dispose() }
  $maintenanceMutex.ReleaseMutex()
  $maintenanceMutexHeld = $false
  Wait-RefreshChildOwnership -Process $refreshChildProcess -Path $refreshOwnershipPath -Token $refreshOwnershipToken -TimeoutSeconds 30 | Out-Null
  $refreshHandoffStarted = $true
  if ($refreshChildProcess.HasExited) {
    $preStartChildState = Read-RefreshTerminalState -Path $journalPath -TransactionId $transactionId -ExpectedVersion $version
    if ($preStartChildState -eq 'RolledBack') {
      $refreshChildRolledBack = $true
      throw 'The protected updater child completed with a verified rollback before transaction start.'
    }
    throw 'The protected updater supervisor exited before transaction start.'
  }
  $maintenanceMutex = New-MaintenanceMutex
  if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) { throw 'The updater supervisor maintenance lock could not be reacquired.' }
  $maintenanceMutexHeld = $true
  # Signal the supervisor before the first risky mutation. If this parent
  # dies, the child can now distinguish a started transaction and restore the
  # service image even when promotion has not completed yet.
  Write-RefreshReady -Path $refreshStartPath -Token $refreshStartToken | Out-Null
  $serviceMutationStarted = $true
  Write-Journal -Path $journalPath -State 'Activating' -Reason 'The protected updater accepted the durable handoff; service and database mutation may now begin.'
  Stop-Service -Name $serviceName -Force
  if (Test-Path -LiteralPath $migrationBundle -PathType Leaf) {
    Write-RefreshReady -Path $databaseMutationPath -Token $databaseMutationToken | Out-Null
    $databaseMutationStarted = $true
    & $migrationBundle --connection "Data Source=$databasePath"
    if ($LASTEXITCODE -ne 0) { throw 'The reviewed database migration bundle failed.' }
  }
  Move-Item -LiteralPath $incomingPath -Destination $newReleasePath
  $newReleasePromoted = $true
  $newExecutable = Join-Path $newReleasePath 'TreadmillRunner.Gateway.exe'
  Set-ServiceBinary -Name $serviceName -ImagePath ('"{0}"' -f $newExecutable)
  Write-Journal -Path $journalPath -State 'Activating' -Reason 'The release is promoted; a staged protected updater refresh must complete before activation is terminal.'
  $maintenanceMutex.ReleaseMutex()
  $maintenanceMutexHeld = $false
  $refreshChildTerminalState = Wait-RefreshChildTerminal -Process $refreshChildProcess -JournalPath $journalPath -TransactionId $transactionId -ExpectedVersion $version -HealthUrl $healthUri.AbsoluteUri -CompletionPath $refreshCompletionPath -CompletionToken $refreshCompletionToken -TimeoutSeconds 180
  if ($refreshChildTerminalState -eq 'RolledBack') {
    $refreshChildRolledBack = $true
    throw 'The protected updater child completed with a verified rollback.'
  }
  if ($refreshChildTerminalState -ne 'Activated') {
    throw 'The protected updater child did not reach an authoritative Activated terminal state.'
  }
  # Activated plus the verified health response is authoritative. Mark this
  # before cleanup so any cleanup interruption cannot enter rollback logic.
  $activationCompleted = $true
  Complete-ActivatedParentCleanup `
    -JournalPath $journalPath `
    -TransactionId $transactionId `
    -ExpectedVersion $version `
    -PlanPath $resolvedPlan `
    -MaintenanceMarkerPath $maintenanceMarkerPath `
    -OwnedArtifactPaths @(
      $incomingPath, $refreshReadyPath, $refreshStartPath, $refreshCompletionPath,
      $refreshOwnershipPath, $databaseMutationPath, $helperBackupPath, $guardianBackupPath,
      $preconditionPath, $helperRefreshPath, $guardianRefreshPath,
      $databaseSwapPath, $helperBackupSwapPath, $guardianBackupSwapPath,
      $helperTargetSwapPath, $guardianTargetSwapPath,
      ("$resolvedPlan.replace-backup"), ("$preconditionPath.replace-backup"),
      ("$refreshReadyPath.replace-backup"), ("$refreshStartPath.replace-backup"),
      ("$refreshCompletionPath.replace-backup"), ("$refreshOwnershipPath.replace-backup"),
      ("$databaseMutationPath.replace-backup"), ("$journalPath.replace-backup"))
  return
}
catch {
  $failure = $_.Exception.Message
  if ($activationCompleted) {
    # A healthy Activated journal must never be rolled back because parent
    # terminal hygiene failed. The finally block makes a best-effort retry.
    throw
  }
  if ($refreshChildRolledBack) {
    # The child has already completed and journaled a verified rollback. The
    # parent must not repeat service/database recovery, but it still owns the
    # transaction workspace cleanup.
    $rollbackCompleted = $true
    throw
  }
  try {
    if ($null -ne $refreshChildProcess) {
      if (-not $refreshChildProcess.HasExited) {
        Stop-Process -Id $refreshChildProcess.Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $refreshChildProcess.Id -Timeout 10 -ErrorAction SilentlyContinue
        $refreshChildProcess.Refresh()
        if (-not $refreshChildProcess.HasExited) {
          throw 'The protected updater child is still running; parent rollback was not started and recovery artifacts were preserved.'
        }
      }
      # Once the child has exited, its journal can no longer change. This is
      # the definitive boundary before parent rollback: Activated remains
      # authoritative even if the 180-second waiter missed its final write.
      $finalChildState = Read-RefreshTerminalState -Path $journalPath -TransactionId $transactionId -ExpectedVersion $version
      if ($finalChildState -eq 'Activated') {
        $activationCompleted = $true
        Complete-ActivatedParentCleanup `
          -JournalPath $journalPath `
          -TransactionId $transactionId `
          -ExpectedVersion $version `
          -PlanPath $resolvedPlan `
          -MaintenanceMarkerPath $maintenanceMarkerPath `
          -OwnedArtifactPaths @(
            $incomingPath, $refreshReadyPath, $refreshStartPath, $refreshCompletionPath,
            $refreshOwnershipPath, $databaseMutationPath, $helperBackupPath, $guardianBackupPath,
            $preconditionPath, $helperRefreshPath, $guardianRefreshPath,
            $databaseSwapPath, $helperBackupSwapPath, $guardianBackupSwapPath,
            $helperTargetSwapPath, $guardianTargetSwapPath,
            ("$resolvedPlan.replace-backup"), ("$preconditionPath.replace-backup"),
            ("$refreshReadyPath.replace-backup"), ("$refreshStartPath.replace-backup"),
            ("$refreshCompletionPath.replace-backup"), ("$refreshOwnershipPath.replace-backup"),
            ("$databaseMutationPath.replace-backup"), ("$journalPath.replace-backup"))
        return
      }
      if ($finalChildState -eq 'RolledBack') {
        $refreshChildRolledBack = $true
        $rollbackCompleted = $true
        throw
      }
    }
    if (-not $maintenanceMutexHeld) {
      if ($null -eq $maintenanceMutex) { $maintenanceMutex = New-MaintenanceMutex }
      if (-not (Wait-MaintenanceMutex -Mutex $maintenanceMutex -TimeoutMilliseconds 30000)) { throw 'The parent could not reacquire the maintenance lock for rollback.' }
      $maintenanceMutexHeld = $true
    }
    if ($serviceMutationStarted -or $newReleasePromoted) {
      Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
    }
    $databaseBackupHash = Get-FileSha256 -Path $databaseBackupPath
      Reconcile-TransactionSwap -Destination $databasePath -ReplacementBackupPath $databaseSwapPath -DurableSourcePath $databaseBackupPath -ExpectedHash $databaseBackupHash
    if ($null -ne $previousHelperHash -and $null -ne $previousGuardianHash) {
      if ($null -ne $helperBackupPath) {
        Reconcile-TransactionSwap -Destination $helperBackupPath -ReplacementBackupPath $helperBackupSwapPath -DurableSourcePath $helperTarget -ExpectedHash $previousHelperHash
      }
      if ($null -ne $guardianBackupPath) {
        Reconcile-TransactionSwap -Destination $guardianBackupPath -ReplacementBackupPath $guardianBackupSwapPath -DurableSourcePath $guardianTarget -ExpectedHash $previousGuardianHash
      }
      Reconcile-TransactionSwap -Destination $helperTarget -ReplacementBackupPath $helperTargetSwapPath -DurableSourcePath $helperBackupPath -ExpectedHash $previousHelperHash
      Reconcile-TransactionSwap -Destination $guardianTarget -ReplacementBackupPath $guardianTargetSwapPath -DurableSourcePath $guardianBackupPath -ExpectedHash $previousGuardianHash
    }
    if ($databaseMutationStarted) {
      Remove-Item -LiteralPath ($databasePath + '-wal') -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath ($databasePath + '-shm') -Force -ErrorAction SilentlyContinue
      Copy-DurableFile -Source $databaseBackupPath -Destination $databasePath -ReplacementBackupPath $databaseSwapPath
      if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf) -or
          (Get-FileSha256 -Path $databasePath) -ne $databaseBackupHash) {
        throw 'The database backup was not restored before rollback cleanup.'
      }
    }
    if ($null -ne $helperBackupPath -and (Test-Path -LiteralPath $helperBackupPath -PathType Leaf)) { Copy-DurableFile -Source $helperBackupPath -Destination $helperTarget -ReplacementBackupPath $helperTargetSwapPath }
    if ($null -ne $guardianBackupPath -and (Test-Path -LiteralPath $guardianBackupPath -PathType Leaf)) { Copy-DurableFile -Source $guardianBackupPath -Destination $guardianTarget -ReplacementBackupPath $guardianTargetSwapPath }
    if ($null -ne $previousHelperHash -and
        (-not (Test-Path -LiteralPath $helperTarget -PathType Leaf) -or
         -not (Test-Path -LiteralPath $guardianTarget -PathType Leaf) -or
         (Get-FileSha256 -Path $helperTarget) -ne $previousHelperHash -or
         (Get-FileSha256 -Path $guardianTarget) -ne $previousGuardianHash)) {
      throw 'The protected updater scripts were not restored before rollback cleanup.'
    }
    if ($serviceMutationStarted -or $newReleasePromoted) {
      if ($null -ne $previousImagePath) {
        Restore-ServiceImageSafely -ServiceName $serviceName `
          -TargetImagePath (Join-Path $newReleasePath 'TreadmillRunner.Gateway.exe') `
          -PreviousImagePath $previousImagePath
      }
      Start-Service -Name $serviceName
      if (-not (Wait-ReleaseHealth -HealthUrl $healthUri.AbsoluteUri -ExpectedVersion '')) {
        throw 'Previous release did not recover readiness.'
      }
      if ($null -ne $previousImagePath) {
        Assert-ServiceUsesImagePath -ServiceName $serviceName -ExpectedImagePath $previousImagePath
      }
    }
    if ($newReleasePromoted) {
      Remove-FailedRelease -NewReleasePath $newReleasePath -ReleaseRoot $releaseRoot -PreviousImagePath $previousImagePath
    }
    Write-Journal -Path $journalPath -State 'RolledBack' -Reason $failure
    $rollbackCompleted = $true
  }
  catch {
    if ($refreshChildRolledBack -and $rollbackCompleted) { throw }
    Write-Journal -Path $journalPath -State 'RollbackFailed' -Reason ("$failure Rollback: $($_.Exception.Message)")
    throw
  }
  throw
}
finally {
  if ($activationCompleted) {
    try {
      Complete-ActivatedParentCleanup `
        -JournalPath $journalPath `
        -TransactionId $transactionId `
        -ExpectedVersion $version `
        -PlanPath $resolvedPlan `
        -MaintenanceMarkerPath $maintenanceMarkerPath `
        -OwnedArtifactPaths @(
          $incomingPath, $refreshReadyPath, $refreshStartPath, $refreshCompletionPath,
          $refreshOwnershipPath, $databaseMutationPath, $helperBackupPath, $guardianBackupPath,
          $preconditionPath, $helperRefreshPath, $guardianRefreshPath,
          $databaseSwapPath, $helperBackupSwapPath, $guardianBackupSwapPath,
          $helperTargetSwapPath, $guardianTargetSwapPath,
          ("$resolvedPlan.replace-backup"), ("$preconditionPath.replace-backup"),
          ("$refreshReadyPath.replace-backup"), ("$refreshStartPath.replace-backup"),
          ("$refreshCompletionPath.replace-backup"), ("$refreshOwnershipPath.replace-backup"),
          ("$databaseMutationPath.replace-backup"), ("$journalPath.replace-backup"))
    }
    catch { }
  }
  if ($rollbackCompleted) {
    if ($maintenanceMarkerCreated -and (Test-Path -LiteralPath $maintenanceMarkerPath)) {
      Remove-OwnedMaintenanceMarker -Path $maintenanceMarkerPath -TransactionId $transactionId
    }
    if ($incomingCreated -and (Test-Path -LiteralPath $incomingPath)) {
      Remove-Item -LiteralPath $incomingPath -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
  if ($maintenanceMutexHeld) {
    $maintenanceMutex.ReleaseMutex()
    $maintenanceMutex.Dispose()
  }
  if ($rollbackCompleted) {
    if ($null -ne $refreshReadyPath) { Remove-Item -LiteralPath $refreshReadyPath -Force -ErrorAction SilentlyContinue }
    if ($null -ne $refreshStartPath) { Remove-Item -LiteralPath $refreshStartPath -Force -ErrorAction SilentlyContinue }
    if ($null -ne $refreshCompletionPath) { Remove-Item -LiteralPath $refreshCompletionPath -Force -ErrorAction SilentlyContinue }
    if ($null -ne $refreshOwnershipPath) { Remove-Item -LiteralPath $refreshOwnershipPath -Force -ErrorAction SilentlyContinue }
    if ($null -ne $databaseMutationPath) { Remove-Item -LiteralPath $databaseMutationPath -Force -ErrorAction SilentlyContinue }
    if ($null -ne $helperBackupPath) { Remove-Item -LiteralPath $helperBackupPath -Force -ErrorAction SilentlyContinue }
    if ($null -ne $guardianBackupPath) { Remove-Item -LiteralPath $guardianBackupPath -Force -ErrorAction SilentlyContinue }
    foreach ($path in @($databaseSwapPath, $helperBackupSwapPath, $guardianBackupSwapPath, $helperTargetSwapPath, $guardianTargetSwapPath)) {
      if ($null -ne $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    }
    foreach ($path in @($resolvedPlan, $preconditionPath, $refreshReadyPath, $refreshStartPath, $refreshCompletionPath, $refreshOwnershipPath, $databaseMutationPath, $journalPath)) {
      if ($null -ne $path) { Remove-Item -LiteralPath ("$path.replace-backup") -Force -ErrorAction SilentlyContinue }
    }
    if ($null -ne $helperRefreshPath) { Remove-Item -LiteralPath $helperRefreshPath -Force -ErrorAction SilentlyContinue }
    if ($null -ne $guardianRefreshPath) { Remove-Item -LiteralPath $guardianRefreshPath -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $resolvedPlan -Force -ErrorAction SilentlyContinue
    if ($null -ne $preconditionPath) { Remove-Item -LiteralPath $preconditionPath -Force -ErrorAction SilentlyContinue }
  }
}
