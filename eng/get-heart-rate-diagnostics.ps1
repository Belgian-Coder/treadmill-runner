[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [Guid] $SessionId,

    [string] $JournalDirectory = (Join-Path $env:ProgramData 'TreadmillRunner\data\diagnostics')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-UtcInstant([object] $Value) {
    if ($Value -is [DateTimeOffset]) { return $Value.ToUniversalTime() }
    if ($Value -is [DateTime]) { return [DateTimeOffset]::new($Value.ToUniversalTime()) }
    return [DateTimeOffset]::Parse([string]$Value,
        [Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
}

# Read retained evidence only. Never contacts Bluetooth, changes the application,
# or interprets an absent record as proof that a sample was lost.
$records = [System.Collections.Generic.List[object]]::new()
$seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
$invalidLines = 0
$filesRead = 0
$lossObserved = $false
$journalNames = @(31..1 | ForEach-Object { "bluetooth.$_.jsonl" }) + @('bluetooth.jsonl')
foreach ($name in $journalNames) {
    $path = Join-Path $JournalDirectory $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
    $stream = $null
    $reader = $null
    try {
        $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        $reader = [IO.StreamReader]::new($stream)
        $filesRead++
        while ($null -ne ($line = $reader.ReadLine())) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            if ($line.Length -gt 65536) { $invalidLines++; continue }
            try {
                $envelope = $line | ConvertFrom-Json -ErrorAction Stop
                $entryProperty = $envelope.PSObject.Properties['Event']
                if ($null -eq $entryProperty -or $null -eq $entryProperty.Value) {
                    $invalidLines++
                    continue
                }
                $entry = $entryProperty.Value
                $at = ConvertTo-UtcInstant $entry.AtUtc
                $dropped = $envelope.PSObject.Properties['DroppedEvents']
                if ($null -ne $dropped -and [long]$dropped.Value -gt 0) { $lossObserved = $true }
                $sessionProperty = $entry.PSObject.Properties['SessionId']
                $matchesSession = $null -ne $sessionProperty -and [string]$sessionProperty.Value -eq $SessionId.ToString()
                if (-not $matchesSession -and [string]$entry.Phase -like 'sample-*') { continue }
                if (-not $seen.Add($line)) { continue }
                $records.Add([pscustomobject]@{ At = $at; Entry = $entry })
            }
            catch { $invalidLines++ }
        }
    }
    catch [IO.FileNotFoundException] {
        # Rotation can move a file between enumeration and opening.
        $invalidLines++
    }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
    }
}

$sessionRecords = @($records | Where-Object {
    $property = $_.Entry.PSObject.Properties['SessionId']
    $null -ne $property -and [string]$property.Value -eq $SessionId.ToString()
} | Sort-Object At)
$samples = [System.Collections.Generic.Dictionary[long, object]]::new()
foreach ($record in $sessionRecords) {
    $entry = $record.Entry
    $sequenceProperty = $entry.PSObject.Properties['SampleSequence']
    if ($null -eq $sequenceProperty -or $null -eq $sequenceProperty.Value) { continue }
    $sequence = [long]$sequenceProperty.Value
    if (-not $samples.ContainsKey($sequence)) {
        $samples[$sequence] = [pscustomobject]@{
            Sequence = $sequence
            HasHeartRate = $null
            Reason = $null
            EnrollmentId = $entry.EnrollmentId
            Generation = $entry.Generation
            Phases = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        }
    }
    $sample = $samples[$sequence]
    $hasHr = $entry.PSObject.Properties['HasHeartRate']
    if ($null -ne $hasHr -and $null -ne $hasHr.Value) { $sample.HasHeartRate = [bool]$hasHr.Value }
    $reason = $entry.PSObject.Properties['Reason']
    if ($null -ne $reason -and $null -ne $reason.Value) { $sample.Reason = [string]$reason.Value }
    [void]$sample.Phases.Add([string]$entry.Phase)
}

$missing = @($samples.Values | Where-Object { $null -ne $_.HasHeartRate -and -not $_.HasHeartRate })
$committed = @($samples.Values | Where-Object { $_.Phases.Contains('sample-committed') })
$discardPhases = @('sample-overflow-replaced', 'sample-writer-completed-discarded',
    'sample-stale-generation-discarded', 'sample-nonretryable-discarded')
$discarded = @($samples.Values | Where-Object {
    $sample = $_
    -not $sample.Phases.Contains('sample-committed') -and
        @($discardPhases | Where-Object { $sample.Phases.Contains($_) }).Count -gt 0
})
$unconfirmed = @($samples.Values | Where-Object {
    $sample = $_
    -not $sample.Phases.Contains('sample-committed') -and
        @($discardPhases | Where-Object { $sample.Phases.Contains($_) }).Count -eq 0
})
$concurrentBle = @()
$from = $null
$to = $null
$captureFrom = $null
$captureTo = $null
if ($sessionRecords.Count -gt 0) {
    $from = $sessionRecords[0].At
    $to = $sessionRecords[-1].At
    $captureTimes = @($sessionRecords | ForEach-Object {
        $captured = $_.Entry.PSObject.Properties['CapturedAtUtc']
        if ($null -ne $captured -and $null -ne $captured.Value) {
            ConvertTo-UtcInstant $captured.Value
        }
        else { $_.At }
    } | Sort-Object)
    $captureFrom = $captureTimes[0]
    $captureTo = $captureTimes[-1]
    $concurrentBle = @($records | Where-Object {
        $_.At -ge $captureFrom.AddSeconds(-30) -and $_.At -le $captureTo.AddSeconds(30) -and
        $_.Entry.Role -eq 'HeartRate' -and
        ([string]$_.Entry.Phase -notlike 'sample-*')
    } | ForEach-Object { $_.Entry } |
        Group-Object Phase, Failure | ForEach-Object {
            [pscustomobject]@{ Event = $_.Name; Count = $_.Count }
        })
}

$warnings = [System.Collections.Generic.List[string]]::new()
if ($samples.Count -eq 0) {
    $warnings.Add('No per-sample evidence was retained. The session may predate this instrumentation, or logs may have rotated; no cause can be inferred.')
}
if ($lossObserved) { $warnings.Add('A retained journal record reports lost diagnostic events; evidence may be incomplete.') }
if ($invalidLines -gt 0) { $warnings.Add('Some records could not be read completely, including possible active-file writes or rotation; evidence may be incomplete.') }
if ($unconfirmed.Count -gt 0) { $warnings.Add('Unconfirmed writes may still be pending, or their outcome may not be retained. They are not automatically classified as lost.') }
$warnings.Add('Concurrent Bluetooth events are observations within the time window, not proof of the physical radio-disconnect cause.')

[pscustomobject]@{
    SessionId = $SessionId
    FilesRead = $filesRead
    InvalidOrPartialRecords = $invalidLines
    JournalLossObserved = $lossObserved
    EvidenceAvailable = ($samples.Count -gt 0)
    FirstEvidenceAtUtc = $from
    FirstCaptureAtUtc = $captureFrom
    LastCaptureAtUtc = $captureTo
    LastEvidenceAtUtc = $to
    ObservedSampleSequences = $samples.Count
    CapturedWithoutHeartRate = $missing.Count
    StoreAcceptedSamples = $committed.Count
    ExplicitlyDiscardedSequences = @($discarded | Sort-Object Sequence | ForEach-Object { $_.Sequence })
    UnconfirmedSequences = @($unconfirmed | Sort-Object Sequence | ForEach-Object { $_.Sequence })
    RetriedSequences = @($samples.Values | Where-Object { $_.Phases.Contains('sample-retry') } |
        Sort-Object Sequence | ForEach-Object { $_.Sequence })
    MissingHeartRateReasons = @($missing | Group-Object Reason | ForEach-Object {
        [pscustomobject]@{ Reason = $_.Name; Samples = $_.Count }
    })
    ConcurrentBluetoothEvents = $concurrentBle
    Warnings = $warnings.ToArray()
} | ConvertTo-Json -Depth 8
