[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string] $Version,
    [string] $OutputRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $projectRoot 'artifacts\releases'
}
$resolvedOutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$allowedDefaultRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts\releases'))
$allowedPrefix = $allowedDefaultRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if ($resolvedOutputRoot -ne $allowedDefaultRoot -and
    -not $resolvedOutputRoot.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Release output must remain under artifacts\releases.'
}
$releaseRoot = Join-Path $resolvedOutputRoot $Version
$publishPath = Join-Path $releaseRoot 'publish'
if (Test-Path -LiteralPath $releaseRoot) {
    throw "Release output already exists and will not be overwritten: $releaseRoot"
}
New-Item -ItemType Directory -Path $publishPath -Force | Out-Null

$headRevision = (& git -C $projectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($headRevision)) { throw 'The source revision could not be determined.' }
$contentLines = [System.Collections.Generic.List[string]]::new()
$contentLines.Add($headRevision)
$contentLines.Add((& git -C $projectRoot diff --binary HEAD -- src Directory.Build.props).ForEach({ [string]$_ }) -join "`n")
$untrackedSource = @(& git -C $projectRoot ls-files --others --exclude-standard -- src Directory.Build.props) | Sort-Object
foreach ($relative in $untrackedSource) {
    $sourcePath = Join-Path $projectRoot $relative
    if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
        $contentLines.Add("$relative=$((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash)")
    }
}
$contentBytes = [System.Text.Encoding]::UTF8.GetBytes(($contentLines -join "`n"))
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $contentHash = $sha256.ComputeHash($contentBytes)
}
finally {
    $sha256.Dispose()
}
$buildId = ([System.BitConverter]::ToString($contentHash)).Replace('-', '').ToLowerInvariant()

Push-Location $projectRoot
try {
    dotnet restore TreadmillRunner.slnx --locked-mode
    if ($LASTEXITCODE -ne 0) { throw 'Locked restore failed.' }
    dotnet publish src\TreadmillRunner.Gateway\TreadmillRunner.Gateway.csproj `
        -c Release --no-restore `
        -p:Version=$Version -p:FileVersion="$Version.0" -p:InformationalVersion="$Version+$buildId" `
        -p:TreadmillRunnerBuildId=$buildId `
        -o $publishPath
    if ($LASTEXITCODE -ne 0) { throw 'Framework-dependent gateway publish failed.' }

    $migrationPath = Join-Path $publishPath 'TreadmillRunner.Migrations.exe'
    # dotnet-ef adds its target RID to project lock files while bundling, even with
    # --no-build. Preserve the exact reviewed lock state so local release creation
    # cannot make the next locked restore fail or leave a dirty worktree.
    $lockSnapshots = @{}
    foreach ($lockFile in Get-ChildItem -LiteralPath (Join-Path $projectRoot 'src') -Recurse -Filter 'packages.lock.json') {
        $lockSnapshots[$lockFile.FullName] = [System.IO.File]::ReadAllBytes($lockFile.FullName)
    }
    try {
        dotnet tool run dotnet-ef migrations bundle `
            --project src\TreadmillRunner.Infrastructure\TreadmillRunner.Infrastructure.csproj `
            --startup-project src\TreadmillRunner.Gateway\TreadmillRunner.Gateway.csproj `
            --configuration Release `
            --output $migrationPath --force --no-build
        if ($LASTEXITCODE -ne 0) { throw 'Reviewed EF Core migration bundle publish failed.' }
    }
    finally {
        foreach ($entry in $lockSnapshots.GetEnumerator()) {
            [System.IO.File]::WriteAllBytes($entry.Key, $entry.Value)
        }
    }

    & (Join-Path $PSScriptRoot 'new-garmin-portable-runtime.ps1') -PublishPath $publishPath
    if ($LASTEXITCODE -ne 0) { throw 'Portable Garmin adapter runtime staging failed.' }

    [System.IO.File]::WriteAllText(
        (Join-Path $publishPath 'build-metadata.json'),
        ([ordered]@{ version = $Version; sourceRevision = $headRevision; buildId = $buildId } | ConvertTo-Json -Compress),
        [System.Text.UTF8Encoding]::new($false))

    foreach ($required in @(
        'TreadmillRunner.Gateway.exe',
        'TreadmillRunner.Gateway.dll',
        'TreadmillRunner.Migrations.exe',
        'Updates\update-helper.ps1',
        'tools\garmin\runtime\python.exe',
        'tools\garmin\runtime\LICENSE.txt',
        'tools\garmin\THIRD-PARTY-NOTICES.md',
        'build-metadata.json',
        'wwwroot\app.css',
        'wwwroot\_framework\blazor.webassembly.js')) {
        if (-not (Test-Path -LiteralPath (Join-Path $publishPath $required) -PathType Leaf)) {
            throw "Published release is missing $required."
        }
    }

    Write-Host "Framework-dependent release $Version published to $publishPath"
}
finally {
    Pop-Location
}
