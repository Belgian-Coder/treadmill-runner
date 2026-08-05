[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [uri] $GatewayUrl,
    [securestring] $WatchToken
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($GatewayUrl.Scheme -ne 'https') { throw 'The watch gateway URL must use HTTPS.' }
if ($null -eq $WatchToken) { $WatchToken = Read-Host 'One-time watch pairing token' -AsSecureString }
$origin = $GatewayUrl.GetLeftPart([System.UriPartial]::Authority).TrimEnd('/')
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($WatchToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($token) -or $token.Length -lt 20) { throw 'A valid watch pairing token is required.' }
    $result = Invoke-RestMethod -Method Get -Uri "$origin/api/watch/status" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 15
    if ([string]::IsNullOrWhiteSpace($result.runnerName) -or [string]::IsNullOrWhiteSpace($result.state)) {
        throw 'The HTTPS endpoint returned an invalid watch status response.'
    }
    Write-Host "Watch HTTPS proof passed for runner '$($result.runnerName)' in state '$($result.state)'."
}
finally {
    if ($null -ne $pointer) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    Remove-Variable token -ErrorAction SilentlyContinue
}
