[CmdletBinding()]
param(
    [ValidateSet('Scan', 'Gatt')]
    [string] $Action = 'Scan',

    [ValidateRange(1, 30)]
    [int] $DurationSeconds = 5,

    [ValidateLength(1, 256)]
    [string] $DeviceId,

    [Uri] $GatewayUri = 'http://localhost:5180/'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Action -eq 'Gatt' -and [string]::IsNullOrWhiteSpace($DeviceId)) {
    throw 'Gatt diagnostics require -DeviceId from a previous passive scan.'
}

$baseUri = $GatewayUri.AbsoluteUri.TrimEnd('/')
switch ($Action) {
    'Scan' {
        $requestUri = "$baseUri/api/diagnostics/ble/scan?durationSeconds=$DurationSeconds"
    }
    'Gatt' {
        $escapedDeviceId = [Uri]::EscapeDataString($DeviceId)
        $requestUri = "$baseUri/api/diagnostics/ble/devices/$escapedDeviceId/gatt"
    }
}

Write-Host "Running read-only BLE diagnostics: $Action"
Write-Host 'This script does not pair, subscribe, write, or control a treadmill.'

$result = Invoke-RestMethod -Method Get -Uri $requestUri
$result | ConvertTo-Json -Depth 8
