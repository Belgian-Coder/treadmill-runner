[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string] $OutputPath,

    [ValidateRange(15, 600)]
    [int] $DurationSeconds = 120,

    [ValidateRange(16, 256)]
    [int] $MaximumFileSizeMb = 64
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Bluetooth ETW capture is supported only on Windows.'
}

$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($currentIdentity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Bluetooth ETW capture requires an elevated PowerShell session.'
}

$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($resolvedOutput) -ne '.etl') {
    throw 'OutputPath must name an .etl file.'
}
$outputDirectory = Split-Path -Parent $resolvedOutput
if ([string]::IsNullOrWhiteSpace($outputDirectory)) {
    throw 'OutputPath must include a directory.'
}
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$traceName = 'TreadmillRunner-Bluetooth-' + [Guid]::NewGuid().ToString('N')
$providers = @(
    # Connection/session and controller diagnostics. HCIRAW is deliberately
    # excluded; the resulting ETL can still contain device identifiers.
    @('Microsoft-Windows-Bluetooth-Bthmini', '0x0000000000000001', '0x5'),
    @('Microsoft-Windows-BTH-BTHPORT', '0x6000000000000003', '0x5'),
    @('Microsoft-Windows-BTH-BTHUSB', '0xC000000000000001', '0x5'),
    @('Microsoft-Windows-Bluetooth-Policy', '0x8000000000000000', '0x5'),
    @('Microsoft-Windows-Bluetooth-BthLEPrepairing', '0x4000000000000000', '0x5'),
    @('Microsoft-Windows-Kernel-PnP', '0x000000000001F000', '0x5')
)
$providerFile = [IO.Path]::GetTempFileName()
$providers |
    ForEach-Object { $_ -join ' ' } |
    Set-Content -LiteralPath $providerFile -Encoding Ascii

$arguments = @(
    'create', 'trace', $traceName,
    '-o', $resolvedOutput,
    '-f', 'bincirc',
    '-max', $MaximumFileSizeMb.ToString([Globalization.CultureInfo]::InvariantCulture),
    '-pf', $providerFile,
    '-ow'
)
$arguments += '-ets'

Write-Warning 'The ETL may contain Bluetooth device identifiers. Keep it local, do not commit or upload it, and sanitize any exported evidence.'
Write-Host "Starting bounded Bluetooth ETW capture for $DurationSeconds seconds: $resolvedOutput"
$started = $false
try {
    & logman.exe @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "logman create failed with exit code $LASTEXITCODE." }
    $started = $true
    Start-Sleep -Seconds $DurationSeconds
}
finally {
    if ($started) {
        & logman.exe stop $traceName -ets | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "logman stop returned exit code $LASTEXITCODE; inspect the collector manually with: logman query $traceName"
        }
    }
    Remove-Item -LiteralPath $providerFile -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $resolvedOutput -PathType Leaf)) {
    throw 'The ETW collector stopped without producing the requested output file.'
}
$file = Get-Item -LiteralPath $resolvedOutput
[pscustomobject]@{
    OutputPath = $file.FullName
    Bytes = $file.Length
    DurationSeconds = $DurationSeconds
    MaximumFileSizeMb = $MaximumFileSizeMb
    ContainsPotentialDeviceIdentifiers = $true
}
