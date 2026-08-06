[CmdletBinding()]
param(
    [string] $EvidenceRoot = '',
    [string] $ShowcaseRoot = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $projectRoot 'docs\project\evidence'
}
if ([string]::IsNullOrWhiteSpace($ShowcaseRoot)) {
    $ShowcaseRoot = Join-Path $projectRoot 'screenshots\showcase'
}

$textFiles = @()
if (Test-Path -LiteralPath $EvidenceRoot -PathType Container) {
    $textFiles = @(Get-ChildItem -LiteralPath $EvidenceRoot -Recurse -File |
        Where-Object { $_.Extension -in '.md', '.json', '.txt', '.yml', '.yaml' })
}

$patterns = [ordered]@{
    'email address' = '(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b'
    'private IPv4 address' = '\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b'
    'Windows source-machine path' = '(?i)(?:\b[A-Z]:[\\/]|[\\/]Users[\\/]|[\\/]home[\\/])'
    'BLE or hardware address' = '(?i)\b(?:[0-9A-F]{2}[:-]){5}[0-9A-F]{2}\b'
    'device GUID' = '(?i)\b[0-9A-F]{8}-[0-9A-F]{4}-[1-5][0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}\b'
    'credential value' = '(?i)\b(?:password|passwd|secret|access[_ -]?token|refresh[_ -]?token|api[_ -]?key)\b\s*[:=]\s*["'']?(?!<|\*|redacted|none|null|not |no )[A-Z0-9_./+\-=]{6,}'
}

$violations = [System.Collections.Generic.List[string]]::new()
foreach ($file in $textFiles) {
    $relative = [System.IO.Path]::GetRelativePath($projectRoot, $file.FullName)
    $content = Get-Content -LiteralPath $file.FullName -Raw
    foreach ($entry in $patterns.GetEnumerator()) {
        if ($content -match $entry.Value) {
            $violations.Add("$relative contains a possible $($entry.Key).")
        }
    }
}

$localDenylist = Join-Path $projectRoot '.public-evidence-denylist'
if (Test-Path -LiteralPath $localDenylist -PathType Leaf) {
    $deniedTerms = @(Get-Content -LiteralPath $localDenylist |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_.Length -ge 3 -and -not $_.StartsWith('#') })
    foreach ($file in $textFiles) {
        $relative = [System.IO.Path]::GetRelativePath($projectRoot, $file.FullName)
        $content = Get-Content -LiteralPath $file.FullName -Raw
        foreach ($term in $deniedTerms) {
            if ($content.IndexOf($term, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $violations.Add("$relative contains a term from the local production-data denylist.")
            }
        }
    }
}

if (Test-Path -LiteralPath $ShowcaseRoot -PathType Container) {
    $unexpected = @(Get-ChildItem -LiteralPath $ShowcaseRoot -Recurse -File |
        Where-Object { $_.Extension.ToLowerInvariant() -notin '.png', '.jpg', '.jpeg', '.webp' })
    foreach ($file in $unexpected) {
        $relative = [System.IO.Path]::GetRelativePath($projectRoot, $file.FullName)
        $violations.Add("$relative is not an approved showcase image type.")
    }
}

if ($violations.Count -gt 0) {
    $violations | Sort-Object -Unique | ForEach-Object { Write-Error $_ }
    throw 'Public evidence sanitization failed.'
}

Write-Host "Public evidence sanitization passed ($($textFiles.Count) text files checked)."
