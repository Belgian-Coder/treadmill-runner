[CmdletBinding()]
param(
    [ValidateSet('Debug', 'Release')]
    [string] $Configuration = 'Release'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$webProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'src\TreadmillRunner.Web'))
$expectedPrefix = $webProjectRoot + [System.IO.Path]::DirectorySeparatorChar
$generatedPaths = @(
    (Join-Path $webProjectRoot "obj\$Configuration\net10.0"),
    (Join-Path $webProjectRoot "bin\$Configuration\net10.0")
)

foreach ($path in $generatedPaths) {
    $resolved = [System.IO.Path]::GetFullPath($path)
    if (-not $resolved.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean generated WebAssembly output outside the Web project: $resolved"
    }

    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

Write-Output "Cleaned generated $Configuration WebAssembly publish state."
