[CmdletBinding()]
param(
    [string] $DeveloperKey,
    [switch] $RequireSdk
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $projectRoot 'connectiq/TreadmillRunnerCompanion'
$manifestPath = Join-Path $appRoot 'manifest.xml'

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Connect IQ manifest is missing.' }
[xml] $manifest = Get-Content -LiteralPath $manifestPath -Raw
$manager = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
$manager.AddNamespace('iq', 'http://www.garmin.com/xml/connectiq')
$application = $manifest.SelectSingleNode('/iq:manifest/iq:application', $manager)
if ($null -eq $application -or $application.type -ne 'watch-app') { throw 'Connect IQ application must be a watch app.' }

$requiredProducts = @('fenix843mm', 'fenix847mm', 'fenix8solar47mm', 'fenix8solar51mm', 'vivoactive5', 'vivoactive6')
$products = @($manifest.SelectNodes('//iq:product', $manager) | ForEach-Object { $_.id })
foreach ($product in $requiredProducts) {
    if ($product -notin $products) { throw "Connect IQ product '$product' is missing." }
}
$permissions = @($manifest.SelectNodes('//iq:uses-permission', $manager) | ForEach-Object { $_.id })
foreach ($permission in @('Fit', 'Communications')) {
    if ($permission -notin $permissions) { throw "Connect IQ permission '$permission' is missing." }
}

$requiredFiles = @(
    'monkey.jungle',
    'source/TreadmillRunnerApp.mc',
    'source/RunnerController.mc',
    'source/RunnerDelegate.mc',
    'source/RunnerView.mc',
    'resources/strings/strings.xml',
    'resources/settings.xml',
    'resources/properties.xml',
    'store/listing.md',
    'store/privacy.md',
    'store/submission-checklist.md'
)
foreach ($relative in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $appRoot $relative) -PathType Leaf)) { throw "Connect IQ project file '$relative' is missing." }
}

$controller = Get-Content -LiteralPath (Join-Path $appRoot 'source/RunnerController.mc') -Raw
$delegate = Get-Content -LiteralPath (Join-Path $appRoot 'source/RunnerDelegate.mc') -Raw
if ($controller -notmatch 'ActivityRecording\.createSession' -or $controller -notmatch 'SUB_SPORT_TREADMILL') {
    throw 'The companion does not create a native treadmill recording.'
}
if ($controller -notmatch 'Sensor\.setEnabledSensors' -or $controller -notmatch 'SENSOR_HEARTRATE') {
    throw 'The companion must enable heart-rate sensors before ActivityRecording starts.'
}
if ($controller -notmatch 'retrySave' -or $delegate -notmatch 'isSavePending') {
    throw 'The companion must preserve and expose retry after a failed activity save.'
}
if ($delegate -notmatch 'onSelect' -or $delegate -match 'onKey.*startRecording') {
    throw 'The companion explicit Select interaction contract is missing or ambiguous.'
}
if ($controller -match 'api/.+(start|speed|incline|pause|stop)') {
    throw 'The Connect IQ companion must not contain treadmill command endpoints.'
}

$monkeyc = Get-Command monkeyc -ErrorAction SilentlyContinue
if ($null -eq $monkeyc) {
    if ($RequireSdk) { throw 'Garmin Connect IQ SDK monkeyc was required but is not installed or not on PATH.' }
    Write-Warning 'Static Connect IQ checks passed. monkeyc is not installed, so PRG/IQ compilation and simulator acceptance remain pending.'
    return
}
if ([string]::IsNullOrWhiteSpace($DeveloperKey) -or -not (Test-Path -LiteralPath $DeveloperKey -PathType Leaf)) {
    throw 'Pass -DeveloperKey <developer_key.der> when monkeyc is installed.'
}

$artifactRoot = Join-Path $projectRoot 'artifacts/connectiq'
New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
foreach ($device in @('fenix847mm', 'vivoactive5')) {
    $output = Join-Path $artifactRoot "TreadmillRunnerCompanion-$device.prg"
    & $monkeyc.Source -f (Join-Path $appRoot 'monkey.jungle') -o $output -y $DeveloperKey -d $device -w
    if ($LASTEXITCODE -ne 0) { throw "Connect IQ compilation failed for $device." }
}
Write-Host 'Connect IQ static checks and representative Fenix 8/Vivoactive builds passed.'
