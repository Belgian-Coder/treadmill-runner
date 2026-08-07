[CmdletBinding()]
param(
    [string] $DeveloperKey,
    [string] $SdkPath,
    [switch] $RequireSdk,
    [switch] $SkipSimulatorTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $projectRoot 'connectiq/TreadmillRunnerCompanion'
$manifestPath = Join-Path $appRoot 'manifest.xml'
$requiredProducts = @('fenix843mm', 'fenix847mm', 'fenix8solar47mm', 'fenix8solar51mm', 'vivoactive5', 'vivoactive6')
$representativeProducts = @('fenix847mm', 'vivoactive5')

function Resolve-ConnectIqSdk {
    param([string] $ExplicitPath)

    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) { $candidates.Add($ExplicitPath) }

    $command = Get-Command monkeyc -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        $candidates.Add((Split-Path -Parent (Split-Path -Parent $command.Source)))
    }

    $currentSdk = Join-Path $env:APPDATA 'Garmin/ConnectIQ/current-sdk.cfg'
    if (Test-Path -LiteralPath $currentSdk -PathType Leaf) {
        $configured = (Get-Content -LiteralPath $currentSdk -Raw).Trim()
        if (-not [string]::IsNullOrWhiteSpace($configured)) { $candidates.Add($configured) }
    }

    foreach ($candidate in $candidates) {
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        if (Test-Path -LiteralPath (Join-Path $resolved 'bin/monkeyc.bat') -PathType Leaf) {
            return $resolved
        }
    }
    return $null
}

function Enable-JavaRuntime {
    $java = Get-Command java -ErrorAction SilentlyContinue
    if ($null -ne $java) { return }

    $javaHome = $env:JAVA_HOME
    if ([string]::IsNullOrWhiteSpace($javaHome)) {
        $javaHome = [Environment]::GetEnvironmentVariable('JAVA_HOME', 'User')
    }
    if (-not [string]::IsNullOrWhiteSpace($javaHome) -and
        (Test-Path -LiteralPath (Join-Path $javaHome 'bin/java.exe') -PathType Leaf)) {
        $env:JAVA_HOME = $javaHome
        $env:PATH = (Join-Path $javaHome 'bin') + [System.IO.Path]::PathSeparator + $env:PATH
        return
    }
    throw 'Connect IQ compilation requires Java 11 or newer, but no Java runtime was found.'
}

function Resolve-DeveloperKey {
    param([string] $ExplicitPath)

    $candidates = @(
        $ExplicitPath,
        $env:TREADMILLRUNNER_CONNECTIQ_DEVELOPER_KEY,
        (Join-Path $env:LOCALAPPDATA 'TreadmillRunner/secrets/connectiq/developer_key.der')
    )
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and
            (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

function Invoke-MonkeyCompiler {
    param(
        [string] $Compiler,
        [string[]] $Arguments,
        [string] $Label
    )

    $output = @(& $Compiler @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $output | ForEach-Object { Write-Host $_ }
    if ($exitCode -ne 0) { throw "$Label failed with exit code $exitCode." }
    if (@($output | Where-Object { "$_" -match '(?i)\bwarning\b' }).Count -gt 0) {
        throw "$Label emitted a compiler warning. Connect IQ release builds must be warning-free."
    }
}

function Invoke-RunNoEvil {
    param(
        [string] $MonkeyDo,
        [string] $Program,
        [string] $Device
    )

    $lastOutput = @()
    for ($attempt = 1; $attempt -le 8; $attempt++) {
        $lastOutput = @(& $MonkeyDo $Program $Device /t 2>&1)
        $exitCode = $LASTEXITCODE
        $text = $lastOutput -join [Environment]::NewLine
        if ($text -match '(?m)^RESULTS\s*$') {
            $lastOutput | ForEach-Object { Write-Host $_ }
            if ($exitCode -ne 0 -or $text -notmatch 'PASSED \((?:passed=\d+, )?(?:failed|failures)=0, errors=0\)') {
                throw "Run No Evil tests failed for $Device."
            }
            return
        }
        if ($attempt -lt 8) { Start-Sleep -Milliseconds 750 }
    }

    $lastOutput | ForEach-Object { Write-Host $_ }
    throw "The Connect IQ simulator did not become ready for $Device."
}

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Connect IQ manifest is missing.' }
[xml] $manifest = Get-Content -LiteralPath $manifestPath -Raw
$manager = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
$manager.AddNamespace('iq', 'http://www.garmin.com/xml/connectiq')
$application = $manifest.SelectSingleNode('/iq:manifest/iq:application', $manager)
if ($null -eq $application -or $application.type -ne 'watch-app') { throw 'Connect IQ application must be a watch app.' }

$products = @($manifest.SelectNodes('//iq:product', $manager) | ForEach-Object { $_.id })
foreach ($product in $requiredProducts) {
    if ($product -notin $products) { throw "Connect IQ product '$product' is missing." }
}
$permissions = @($manifest.SelectNodes('//iq:uses-permission', $manager) | ForEach-Object { $_.id })
foreach ($permission in @('Fit', 'Communications', 'Sensor')) {
    if ($permission -notin $permissions) { throw "Connect IQ permission '$permission' is missing." }
}

$requiredFiles = @(
    'monkey.jungle',
    'source/TreadmillRunnerApp.mc',
    'source/RunnerController.mc',
    'source/RunnerDelegate.mc',
    'source/RunnerView.mc',
    'source/GatewaySettings.mc',
    'source/RunnerFormatting.mc',
    'tests/RunnerPureTests.mc',
    'resources/strings/strings.xml',
    'resources/settings.xml',
    'resources/properties.xml',
    'store/listing.md',
    'store/privacy.md',
    'store/submission-checklist.md'
)
foreach ($relative in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $appRoot $relative) -PathType Leaf)) {
        throw "Connect IQ project file '$relative' is missing."
    }
}

$controller = Get-Content -LiteralPath (Join-Path $appRoot 'source/RunnerController.mc') -Raw
$delegate = Get-Content -LiteralPath (Join-Path $appRoot 'source/RunnerDelegate.mc') -Raw
$tests = Get-Content -LiteralPath (Join-Path $appRoot 'tests/RunnerPureTests.mc') -Raw
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
if (@([regex]::Matches($tests, '\(:test\)')).Count -lt 3) {
    throw 'The Connect IQ companion must retain its Run No Evil test coverage.'
}

$resolvedSdk = Resolve-ConnectIqSdk -ExplicitPath $SdkPath
if ($null -eq $resolvedSdk) {
    if ($RequireSdk) { throw 'Garmin Connect IQ SDK monkeyc was required but was not found.' }
    Write-Warning 'Static Connect IQ checks passed. Install the SDK to compile and run simulator tests.'
    return
}

Enable-JavaRuntime
$resolvedKey = Resolve-DeveloperKey -ExplicitPath $DeveloperKey
if ($null -eq $resolvedKey) {
    throw 'A protected Connect IQ developer key is required. Pass -DeveloperKey or set TREADMILLRUNNER_CONNECTIQ_DEVELOPER_KEY.'
}

$monkeyc = Join-Path $resolvedSdk 'bin/monkeyc.bat'
$monkeydo = Join-Path $resolvedSdk 'bin/monkeydo.bat'
$simulator = Join-Path $resolvedSdk 'bin/simulator.exe'
$sdkVersion = ((@(& $monkeyc -v 2>&1) | ForEach-Object { "$_" }) -join ' ').Trim()
Write-Host "Using $sdkVersion"

$artifactRoot = Join-Path $projectRoot 'artifacts/connectiq'
New-Item -ItemType Directory -Path $artifactRoot -Force | Out-Null
$buildEvidence = [System.Collections.Generic.List[object]]::new()
foreach ($device in $requiredProducts) {
    $output = Join-Path $artifactRoot "TreadmillRunnerCompanion-$device.prg"
    Invoke-MonkeyCompiler -Compiler $monkeyc -Label "Connect IQ $device build" -Arguments @(
        '-f', (Join-Path $appRoot 'monkey.jungle'),
        '-o', $output,
        '-y', $resolvedKey,
        '-d', $device,
        '-w'
    )
    $file = Get-Item -LiteralPath $output
    $buildEvidence.Add([ordered]@{
        device = $device
        bytes = $file.Length
        sha256 = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
    })
}

$testEvidence = [System.Collections.Generic.List[object]]::new()
if (-not $SkipSimulatorTests) {
    $existingSimulator = Get-Process simulator -ErrorAction SilentlyContinue | Select-Object -First 1
    $startedSimulator = $null
    if ($null -eq $existingSimulator) {
        $startedSimulator = Start-Process -FilePath $simulator -WindowStyle Hidden -PassThru
        Start-Sleep -Milliseconds 1500
    }
    try {
        foreach ($device in $representativeProducts) {
            $testProgram = Join-Path $artifactRoot "TreadmillRunnerCompanion-$device-tests.prg"
            Invoke-MonkeyCompiler -Compiler $monkeyc -Label "Connect IQ $device unit-test build" -Arguments @(
                '-f', (Join-Path $appRoot 'monkey.jungle'),
                '-o', $testProgram,
                '-y', $resolvedKey,
                '-d', $device,
                '-w',
                '-t'
            )
            Invoke-RunNoEvil -MonkeyDo $monkeydo -Program $testProgram -Device $device
            $testEvidence.Add([ordered]@{ device = $device; tests = 3; result = 'passed' })
        }
    }
    finally {
        if ($null -ne $startedSimulator -and -not $startedSimulator.HasExited) {
            Stop-Process -Id $startedSimulator.Id
        }
    }
}

$validation = [ordered]@{
    schemaVersion = 1
    sdk = $sdkVersion
    developerKeySha256 = (Get-FileHash -LiteralPath $resolvedKey -Algorithm SHA256).Hash
    builds = $buildEvidence
    simulatorTests = $testEvidence
    simulatorTestsSkipped = [bool]$SkipSimulatorTests
}
$validation | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $artifactRoot 'connectiq-validation.json') -Encoding utf8
Write-Host 'Connect IQ static checks, all declared device builds, and requested simulator tests passed.'
