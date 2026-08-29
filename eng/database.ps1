[CmdletBinding()]
param(
    [ValidateSet('Status', 'Add', 'Script', 'Update')]
    [string] $Action = 'Status',
    [string] $MigrationName,
    [string] $DatabasePath,
    [string] $OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'path-helpers.ps1')
$designProject = Join-Path $projectRoot 'src/TreadmillRunner.Infrastructure/Design/TreadmillRunner.Infrastructure.Design.csproj'

function Invoke-DotNet {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]] $Arguments)

    & dotnet @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "dotnet $($Arguments -join ' ') failed."
    }
}

Push-Location $projectRoot
try {
    Invoke-DotNet tool restore
    Invoke-DotNet restore $designProject --locked-mode
    Invoke-DotNet build $designProject --configuration Release --no-restore

    $efArguments = @(
        'tool', 'run', 'dotnet-ef',
        '--',
        '--project', $designProject,
        '--startup-project', $designProject,
        '--configuration', 'Release',
        '--no-build'
    )

    switch ($Action) {
        'Status' {
            Invoke-DotNet @efArguments migrations list
        }
        'Add' {
            if ([string]::IsNullOrWhiteSpace($MigrationName)) {
                throw '-MigrationName is required for the Add action.'
            }

            Invoke-DotNet @efArguments migrations add $MigrationName `
                --output-dir '../Persistence/Migrations' `
                --namespace 'TreadmillRunner.Infrastructure.Persistence.Migrations'

            # The design project compiles persistence sources from a sibling folder. EF therefore
            # writes its updated snapshot under a namespace-derived folder inside Design even
            # though the migration itself honors --output-dir. Promote that generated snapshot to
            # the canonical compiled migrations folder so pending-model checks remain trustworthy.
            $designDirectory = Split-Path -Parent $designProject
            $generatedSnapshot = Join-Path $designDirectory 'TreadmillRunner/Infrastructure/Persistence/Migrations/TreadmillRunnerDbContextModelSnapshot.cs'
            $canonicalSnapshot = Join-Path $projectRoot 'src/TreadmillRunner.Infrastructure/Persistence/Migrations/TreadmillRunnerDbContextModelSnapshot.cs'
            if (Test-Path -LiteralPath $generatedSnapshot) {
                Move-Item -LiteralPath $generatedSnapshot -Destination $canonicalSnapshot -Force
                Write-Host "Model snapshot promoted to $canonicalSnapshot"
            }
        }
        'Script' {
            if ([string]::IsNullOrWhiteSpace($OutputPath)) {
                $OutputPath = Join-Path $projectRoot 'artifacts/database/treadmillrunner.sql'
            }

            $resolvedOutput = Resolve-FullPath -Path $OutputPath -BasePath $projectRoot
            $outputDirectory = Split-Path -Parent $resolvedOutput
            New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
            Invoke-DotNet @efArguments migrations script --output $resolvedOutput
            Write-Host "Migration script written to $resolvedOutput"
        }
        'Update' {
            if ([string]::IsNullOrWhiteSpace($DatabasePath)) {
                throw '-DatabasePath is required for the Update action.'
            }

            $resolvedDatabase = Resolve-FullPath -Path $DatabasePath -BasePath $projectRoot
            $databaseDirectory = Split-Path -Parent $resolvedDatabase
            New-Item -ItemType Directory -Path $databaseDirectory -Force | Out-Null
            $connectionString = "Data Source=$resolvedDatabase;Foreign Keys=True;Default Timeout=5"
            Invoke-DotNet @efArguments database update --connection $connectionString
        }
    }
}
finally {
    Pop-Location
}
