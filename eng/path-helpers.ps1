Set-StrictMode -Version Latest

function Resolve-FullPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $Path,
        [Parameter(Mandatory)]
        [string] $BasePath
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function Test-PathWithinRoot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string] $Path,
        [Parameter(Mandatory)]
        [string] $Root
    )

    $separators = [char[]]@(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar)
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd($separators)
    $comparison = [System.StringComparison]::OrdinalIgnoreCase

    if ($resolvedPath.Equals($resolvedRoot, $comparison)) {
        return $true
    }

    $rootPrefix = $resolvedRoot + [System.IO.Path]::DirectorySeparatorChar
    return $resolvedPath.StartsWith($rootPrefix, $comparison)
}

function ConvertTo-NativeArgumentString {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyCollection()][string[]] $Arguments)

    $quoted = foreach ($argument in $Arguments) {
        if ($argument.Length -gt 0 -and $argument -notmatch '[\s"]') {
            $argument
            continue
        }

        $builder = [System.Text.StringBuilder]::new()
        [void] $builder.Append([char]34)
        $backslashes = 0
        foreach ($character in $argument.ToCharArray()) {
            if ($character -eq [char]92) {
                $backslashes++
                continue
            }
            if ($character -eq [char]34) {
                if ($backslashes -gt 0) { [void] $builder.Append([char]92, $backslashes * 2) }
                [void] $builder.Append([char]92)
                [void] $builder.Append([char]34)
                $backslashes = 0
                continue
            }
            if ($backslashes -gt 0) { [void] $builder.Append([char]92, $backslashes) }
            [void] $builder.Append($character)
            $backslashes = 0
        }
        if ($backslashes -gt 0) { [void] $builder.Append([char]92, $backslashes * 2) }
        [void] $builder.Append([char]34)
        $builder.ToString()
    }
    return $quoted -join ' '
}

function Set-NativeProcessArguments {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][System.Diagnostics.ProcessStartInfo] $StartInfo,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]] $Arguments
    )

    if ($null -ne $StartInfo.PSObject.Properties['ArgumentList']) {
        foreach ($argument in $Arguments) { $StartInfo.ArgumentList.Add($argument) }
        return
    }
    $StartInfo.Arguments = ConvertTo-NativeArgumentString -Arguments $Arguments
}

function Stop-NativeProcessTree {
    [CmdletBinding()]
    param([Parameter(Mandatory)][System.Diagnostics.Process] $Process)

    if ($Process.HasExited) { return }
    $treeKill = $Process.GetType().GetMethod('Kill', [Type[]]@([bool]))
    if ($null -ne $treeKill) {
        $Process.Kill($true)
        return
    }

    & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0 -and -not $Process.HasExited) { $Process.Kill() }
}
