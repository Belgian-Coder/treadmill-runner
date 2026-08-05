[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateSet('Stop', 'Pause', 'SetIncline', 'SetSpeed', 'Start', 'StartStop', 'DailyControlSequence')]
  [string]$Stage,

  [Parameter(Mandatory)]
  [Guid]$OperationId,

  [Guid]$StopOperationId,

  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$ExpectedModel,

  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$ExpectedFirmware,

  [Parameter(Mandatory)]
  [ValidateLength(1, 100)]
  [string]$Observer,

  [double]$Target,

  [string]$DatabasePath = (Join-Path $PSScriptRoot '..\data\treadmillrunner.db')
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$resolvedDatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)
$gatewayProject = Join-Path $projectRoot 'src\TreadmillRunner.Gateway\TreadmillRunner.Gateway.csproj'

if ($OperationId -eq [Guid]::Empty) {
  throw 'OperationId must be a newly generated non-empty GUID. It is durably consumed before any command write.'
}
if ($Stage -eq 'StartStop' -and $StopOperationId -eq [Guid]::Empty) {
  throw 'StartStop requires -StopOperationId with a second newly generated GUID.'
}
if ($Stage -ne 'StartStop' -and $PSBoundParameters.ContainsKey('StopOperationId')) {
  throw '-StopOperationId is valid only for StartStop.'
}
if ($Stage -eq 'StartStop' -and $OperationId -eq $StopOperationId) {
  throw 'Start and Stop operation IDs must be different.'
}

$requiresTarget = $Stage -in @('SetIncline', 'SetSpeed')
if ($requiresTarget -and -not $PSBoundParameters.ContainsKey('Target')) {
  throw "$Stage requires -Target."
}
if (-not $requiresTarget -and $PSBoundParameters.ContainsKey('Target')) {
  throw "$Stage does not accept -Target."
}

$processEnvironment = @{
  'Commissioning__Mode' = switch ($Stage) {
    'StartStop' { 'FtmsStartStop' }
    'DailyControlSequence' { 'FtmsDailyControlSequence' }
    default { 'FtmsCommand' }
  }
  'Commissioning__OperationId' = $OperationId.ToString('D')
  'Commissioning__ExpectedModel' = $ExpectedModel
  'Commissioning__ExpectedFirmware' = $ExpectedFirmware
  'Commissioning__Observer' = $Observer
  'Persistence__DatabasePath' = $resolvedDatabasePath
}
if ($Stage -eq 'StartStop') {
  $processEnvironment['Commissioning__StopOperationId'] = $StopOperationId.ToString('D')
}
elseif ($Stage -ne 'DailyControlSequence') {
  $processEnvironment['Commissioning__Command'] = $Stage
}
if ($requiresTarget) {
  $processEnvironment['Commissioning__Target'] = $Target.ToString(
    [System.Globalization.CultureInfo]::InvariantCulture)
}

$arguments = @('run', '--project', $gatewayProject, '--configuration', 'Release', '--no-build')
$process = Start-Process `
  -FilePath 'dotnet' `
  -ArgumentList $arguments `
  -Environment $processEnvironment `
  -WorkingDirectory $projectRoot `
  -Wait `
  -NoNewWindow `
  -PassThru

exit $process.ExitCode
