[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'path-helpers.ps1')

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'treadmillrunner-path-contract'
$inside = Join-Path $testRoot 'artifacts\test-results'
$sibling = "$testRoot-outside\artifacts"
$parent = Join-Path $testRoot '..\outside'

$relative = Resolve-FullPath -Path 'artifacts\test-results' -BasePath $testRoot
$expectedRelative = [System.IO.Path]::GetFullPath($inside)
if (-not $relative.Equals($expectedRelative, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Relative path resolution did not use the supplied base path.'
}
if (-not (Test-PathWithinRoot -Path $testRoot -Root $testRoot)) {
    throw 'The repository root must be accepted as inside itself.'
}
if (-not (Test-PathWithinRoot -Path $inside -Root $testRoot)) {
    throw 'A repository descendant must be accepted.'
}
if (Test-PathWithinRoot -Path $sibling -Root $testRoot) {
    throw 'A sibling sharing the repository name prefix must be rejected.'
}
if (Test-PathWithinRoot -Path $parent -Root $testRoot) {
    throw 'A path outside the repository must be rejected.'
}

$startInfo = [System.Diagnostics.ProcessStartInfo]::new('dotnet')
$nativeArguments = @('test', 'two words', 'x|y', 'quote"value', 'trail \')
Set-NativeProcessArguments -StartInfo $startInfo -Arguments $nativeArguments
if ($null -ne $startInfo.PSObject.Properties['ArgumentList']) {
    if (($startInfo.ArgumentList -join "`n") -ne ($nativeArguments -join "`n")) {
        throw 'Modern native-process arguments were not preserved exactly.'
    }
}
elseif ($startInfo.Arguments -ne 'test "two words" x|y "quote\"value" "trail \\"') {
    throw "Windows PowerShell native-process quoting is invalid: $($startInfo.Arguments)"
}

Write-Host 'Path helper contracts passed.'
