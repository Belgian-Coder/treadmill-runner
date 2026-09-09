---
title: Heart-rate gap diagnostics
type: operations
status: reviewed
owner: project
audience: operator-and-developer
updated: 2026-09-10
---

# Heart-rate gap diagnostics

After installing a build containing this instrumentation, hardware sessions automatically record evidence from Bluetooth reception through sample storage. No diagnostic mode or hardware command is required. Historical gaps cannot gain evidence retrospectively.

For an ordinary run, no preparation is required. Wear and wake the strap as usual, then start the run without refreshing Devices or pressing Connect. If heart rate does not appear, first note the exact local time and what the app shows; avoid changing Bluetooth state until that timestamp is recorded. After the run, retain the session ID shown in History. Together, the timestamp and session ID let the automatic journal distinguish demand, discovery, connection, notification, selection, sample creation, and persistence stages.

When investigating an intermittent startup or reconnect problem, an optional elevated Windows trace can cover the first ten minutes of the run. Start it immediately before opening the run:

```powershell
./eng/capture-bluetooth-etw.ps1 `
  -OutputPath 'C:\ProgramData\TreadmillRunner\data\diagnostics\captures\polar-run.etl' `
  -DurationSeconds 600
```

The automatic journal remains the primary session-correlated record. The ETW adds Windows controller and Bluetooth-provider timing that the application cannot observe. Keep the ETL local because Windows events can contain device identifiers.

Run this read-only report from the repository on the service host, using the session ID from History or the session export:

```powershell
./eng/get-heart-rate-diagnostics.ps1 -SessionId '00000000-0000-0000-0000-000000000000'
```

For a copied journal or a custom database directory, add `-JournalDirectory 'D:\Evidence\diagnostics'`. To save the JSON report, redirect stdout to a local file. The helper reads files only; it does not connect to Bluetooth, call the application or modify the database.

The report distinguishes:

- Samples captured without heart rate, grouped by observed source availability, readiness, quality or freshness.
- Samples accepted by the session store, retried, or explicitly discarded by the writer.
- Writes with no retained outcome, reported as unconfirmed rather than assumed lost.
- Bluetooth failures and stage events observed from 30 seconds before the first retained capture to 30 seconds after the last retained capture. A delayed store outcome does not extend this window.

The raw JSONL records retain capture and outcome UTC timestamps, session ID and sample sequence, so storage delay can be separated from missing heart rate at capture. `sample-committed` means the store operation returned successfully; the report does not independently query SQLite. Bluetooth records include first notification before parsing, per-attempt notification/valid/contact-loss/invalid counters, maximum notification intervals, distinct initial/established silence stages, rediscovery outcomes and previous source context. Failure records also retain an allow-listed exception type, GATT communication status, ATT error, disconnect origin, GATT-session status/error and cancellation/disposal ordering when Windows supplies them. `ConcurrentBluetoothFailures` exposes those safe fields in the report. Neither the journal nor report contains exception messages, heart-rate values, raw payloads, names or device addresses.

The journal starts with `journal-started`. Check `GET /api/diagnostics/ble/journal` for its last successful write and dropped/storage-failure counters. Files are beside the configured database, normally `C:\ProgramData\TreadmillRunner\data\diagnostics`. Retention is approximately 64 MiB across 32 files; duration depends on event volume. Save a copy soon after a problematic session. Partial records, reported journal loss and missing outcomes reduce confidence, and rotation can remove older evidence.

A native disconnect identifies what Windows reported. It does not prove whether interference, sensor power, the adapter, its driver or another physical cause triggered it. Concurrent events establish timing, not physical causation. The report also cannot prove losses before a session sample was created.

For a repeatable owner-approved reproduction on the Windows service host, an elevated operator can collect a bounded controller/session trace without sending a Bluetooth command:

```powershell
./eng/capture-bluetooth-etw.ps1 `
  -OutputPath 'C:\ProgramData\TreadmillRunner\data\diagnostics\captures\ble-reproduction.etl' `
  -DurationSeconds 120
```

The helper caps duration at ten minutes and uses a bounded circular ETL. It deliberately excludes the raw-HCI keyword, but Windows provider events can still contain Bluetooth device identifiers. Keep the ETL local, never commit or upload it, and sanitize any exported evidence. Starting a capture does not activate a sensor or create a hardware test window; the owner must separately approve and prepare any passive connection/notification reproduction.
