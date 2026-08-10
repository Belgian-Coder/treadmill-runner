[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$PlanPath,
  [Parameter(Mandatory)][string]$InstallRoot,
  [Parameter(Mandatory)][string]$DataRoot,
  [ValidatePattern('^http://(127\.0\.0\.1|localhost)(:\d+)?/')][string]$HealthUrl = 'http://127.0.0.1:5180/health/ready'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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
  $payload = [ordered]@{
    schemaVersion = 1
    transactionId = [string]$plan.TransactionId
    version = [string]$plan.Version
    state = $State
    occurredAtUtc = [DateTimeOffset]::UtcNow.ToString('O')
    reason = $Reason
  } | ConvertTo-Json
  $temporary = "$Path.tmp"
  [System.IO.File]::WriteAllText($temporary, $payload, [System.Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $temporary -Destination $Path -Force
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

Start-Sleep -Seconds 2
$resolvedPlan = [System.IO.Path]::GetFullPath($PlanPath)
$installRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$dataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$updaterRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
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
$actualHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash
if ($actualHash -ne ([string]$manifest.packageSha256).ToUpperInvariant()) {
  throw 'The staged package hash changed after verification.'
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$newReleasePath = Assert-UnderRoot -Path (Join-Path $releaseRoot $version) -Root $releaseRoot
if (Test-Path -LiteralPath $newReleasePath) { throw 'The immutable target release already exists.' }
$incomingPath = Assert-UnderRoot -Path (Join-Path $releaseRoot ('.incoming-' + $transactionId)) -Root $releaseRoot
if (Test-Path -LiteralPath $incomingPath) { throw 'The update transaction workspace already exists.' }
New-Item -ItemType Directory -Path $incomingPath | Out-Null
$incomingCreated = $true
$previousImagePath = $null
$maintenanceMarkerCreated = $false
try {
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

  $newExecutable = Join-Path $incomingPath 'TreadmillRunner.Gateway.exe'
  if (-not (Test-Path -LiteralPath $newExecutable -PathType Leaf)) { throw 'The release executable is missing.' }
  $migrationBundle = Join-Path $incomingPath 'TreadmillRunner.Migrations.exe'

  $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
  if ($null -eq $service) { throw 'The gateway service is not installed.' }
  $previousImagePath = [string]$service.PathName
  $currentExecutable = if ($previousImagePath.StartsWith('"')) {
    $previousImagePath.Substring(1, $previousImagePath.IndexOf('"', 1) - 1)
  } elseif (Test-Path -LiteralPath $previousImagePath -PathType Leaf) {
    $previousImagePath
  } else {
    throw 'The installed service binary path is not a single executable path.'
  }
  $currentExecutable = Assert-UnderRoot -Path $currentExecutable -Root $releaseRoot
  if ([System.IO.Path]::GetFileName($currentExecutable) -ne 'TreadmillRunner.Gateway.exe') {
    throw 'The installed service executable is outside the immutable release contract.'
  }
  $currentReleasePath = Split-Path -Parent $currentExecutable
  $currentVersionText = Split-Path -Leaf $currentReleasePath
  if ($currentVersionText -notmatch '^\d+\.\d+\.\d+$') {
    throw 'The installed release directory has an invalid version.'
  }
  $currentVersion = [Version]$currentVersionText
  if ([Version]$version -le $currentVersion) { throw 'The signed release is not newer than the installed release.' }
  [System.IO.File]::WriteAllText(
    $maintenanceMarkerPath,
    "update $transactionId $([DateTimeOffset]::UtcNow.ToString('O'))",
    [System.Text.UTF8Encoding]::new($false))
  $maintenanceMarkerCreated = $true
  Stop-Service -Name $serviceName -Force
  if (Test-Path -LiteralPath $migrationBundle -PathType Leaf) {
    & $migrationBundle --connection "Data Source=$databasePath"
    if ($LASTEXITCODE -ne 0) { throw 'The reviewed database migration bundle failed.' }
  }
  Move-Item -LiteralPath $incomingPath -Destination $newReleasePath
  $newExecutable = Join-Path $newReleasePath 'TreadmillRunner.Gateway.exe'
  Set-ServiceBinary -Name $serviceName -ImagePath ('"{0}"' -f $newExecutable)
  Start-Service -Name $serviceName
  if (-not (Wait-ReleaseHealth -HealthUrl $healthUri.AbsoluteUri -ExpectedVersion $version)) {
    throw 'The promoted release did not report the expected version and readiness within 120 seconds.'
  }
  Write-Journal -Path $journalPath -State 'Activated' -Reason 'Expected version and readiness were confirmed.'
}
catch {
  $failure = $_.Exception.Message
  Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath ($databasePath + '-wal') -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath ($databasePath + '-shm') -Force -ErrorAction SilentlyContinue
  Copy-Item -LiteralPath $databaseBackupPath -Destination $databasePath -Force
  if ($null -ne $previousImagePath) { Set-ServiceBinary -Name $serviceName -ImagePath $previousImagePath }
  Start-Service -Name $serviceName
  if (-not (Wait-ReleaseHealth -HealthUrl $healthUri.AbsoluteUri -ExpectedVersion '')) {
    Write-Journal -Path $journalPath -State 'RollbackFailed' -Reason 'Previous release did not recover readiness.'
    throw
  }
  Write-Journal -Path $journalPath -State 'RolledBack' -Reason $failure
  throw
}
finally {
  if ($maintenanceMarkerCreated -and (Test-Path -LiteralPath $maintenanceMarkerPath)) {
    Remove-Item -LiteralPath $maintenanceMarkerPath -Force -ErrorAction SilentlyContinue
  }
  if ($incomingCreated -and (Test-Path -LiteralPath $incomingPath)) {
    Remove-Item -LiteralPath $incomingPath -Recurse -Force -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $resolvedPlan -Force -ErrorAction SilentlyContinue
}
