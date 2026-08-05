---
title: Omega Z Fixed Validation Runbook
type: runbook
status: prepared-hardware-pending
owner: project
audience: owner-and-operator
updated: 2026-08-04
---

# Omega Z fixed validation runbook

This is the complete sequence for the next owner-attended session. Implementation is frozen before
the sequence begins. Do not add a new command, retry a result, or change protocol mode during the
session. The commissioning process issues at most one motion-affecting command per invocation and
durably consumes its operation ID before attempting the write.

This runbook is the validation contract. The next session performs it as written; it does not add
code, additional gates, exploratory probes, or a different command order.

## Fixed identity and parameters

- Model: `OMEGA Z`
- Console software: `S3.02` (owner-observed during power-on)
- Bluetooth firmware: `V10.23.17` (read from Device Information)
- Protocol and telemetry: FTMS only
- Reported speed: `0.8–20.0 km/h`, `0.1 km/h` increment
- Reported incline: `0–12%`, `0.1%` increment
- Observer argument: `owner-present`
- Database: `data/treadmillrunner.db`

At the start of the sequence: treadmill powered, belt empty, safety key fitted, physical Stop
reachable, and the normal TreadmillRunner service/gateway stopped so it does not compete for BLE.
These conditions apply to the whole sequence and are not re-requested between stages.

## One-time shell setup

```powershell
Set-Location <path-to-treadmill-runner>
$omega = @{
  ExpectedModel = 'OMEGA Z'
  ExpectedFirmware = 'V10.23.17'
  Observer = 'owner-present'
  DatabasePath = (Join-Path (Get-Location) 'data\treadmillrunner.db')
}
```

## Exact command sequence

Run each command once and retain its JSON output. `Confirmed` advances to the next numbered item.
`Rejected` means correct the stated pre-write condition and use a new GUID. `Unknown` ends the
motion sequence immediately; use physical Stop, inspect the treadmill, and do not retry.

1. Confirm the stopped control surface without moving the belt.

   ```powershell
   & .\eng\commission-omega-z.ps1 -Stage Stop -OperationId ([guid]::NewGuid()) @omega
   ```

2. With the belt still empty and stopped, set incline to `0.1%`, then return to `0.0%`.

   ```powershell
   & .\eng\commission-omega-z.ps1 -Stage SetIncline -Target 0.1 -OperationId ([guid]::NewGuid()) @omega
   & .\eng\commission-omega-z.ps1 -Stage SetIncline -Target 0.0 -OperationId ([guid]::NewGuid()) @omega
   ```

3. For a bounded Start/Stop timing trial, keep one gateway process alive, start the empty belt at
   the device-reported minimum `0.8 km/h`, wait three seconds after confirmation, and stop it.

   ```powershell
   & .\eng\commission-omega-z.ps1 `
     -Stage StartStop `
     -OperationId ([guid]::NewGuid()) `
     -StopOperationId ([guid]::NewGuid()) `
     @omega
   ```

   Retain the structured latency fields. Do not reproduce the obsolete two-process wrapper; its
   second gateway startup caused an observed approximately 13-second moving window.

4. Start again at `0.8 km/h` to prepare the separate speed and Pause stages.

   ```powershell
   & .\eng\commission-omega-z.ps1 -Stage Start -OperationId ([guid]::NewGuid()) @omega
   ```

5. While it is moving at `0.8 km/h`, set speed to `1.0 km/h`.

   ```powershell
   & .\eng\commission-omega-z.ps1 -Stage SetSpeed -Target 1.0 -OperationId ([guid]::NewGuid()) @omega
   ```

6. Pause from `1.0 km/h` and confirm fresh telemetry reaches `0.0 km/h`.

   ```powershell
   & .\eng\commission-omega-z.ps1 -Stage Pause -OperationId ([guid]::NewGuid()) @omega
   ```

7. Resume using Start/Resume and confirm `0.8 km/h`.

   ```powershell
   & .\eng\commission-omega-z.ps1 -Stage Start -OperationId ([guid]::NewGuid()) @omega
   ```

8. Stop the moving belt and confirm fresh telemetry reaches `0.0 km/h` after the Resume stage.

   ```powershell
   & .\eng\commission-omega-z.ps1 -Stage Stop -OperationId ([guid]::NewGuid()) @omega
   ```

## Fixed daily-use acceptance after command commissioning

Restart the normal gateway, then perform one representative workout from the browser:

1. Arm and remote Start at `0.8 km/h`.
2. Confirm a planned speed transition and planned incline transition.
3. Confirm Polar H10 telemetry is no more than five seconds old.
4. Run HR control in Shadow, then Decrease only, then Full; manually change speed once and confirm
   automation changes to `SuspendedManualOverride` until explicitly re-enabled.
5. Pause, Resume, and Stop.
6. Download the session CSV and FIT Activity and open History after a browser reconnect.
7. Restart the service during an armed test session and confirm the session becomes Interrupted;
   no command or session resumes.

## Fixed operational acceptance

1. Observe simultaneous Omega/Polar read-only telemetry for 5–10 minutes, then perform one treadmill power-cycle/reconnect check. Stop the test at 10 minutes; longer physical soak or repeated power-cycle requirements are intentionally deferred to normal daily-use observation.
2. Reboot the Windows VM and prove both subscriptions recover before login.
3. Confirm the phone/tablet dashboard connects and updates during ordinary household use. Do not run a formal latency benchmark; inspect retained timestamps only when diagnosing visible delay.
4. Download a full backup; preview and restore it while idle; verify a clean-install restore.
5. Stage and activate signed release B from release A; stage broken C and retain helper evidence that
   health failure restores the previous directory and database automatically.

No hardware validation in this runbook was executed while the owner was away.
