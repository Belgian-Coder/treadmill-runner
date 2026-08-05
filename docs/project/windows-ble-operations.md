---
title: Windows BLE Read-Only Operations
type: operations-guide
status: active
owner: project
audience: operator-and-developer
updated: 2026-08-04
---

# Windows BLE read-only operations

TR-002 adds short-lived diagnostics for proving Windows Bluetooth availability. They only observe nearby advertisements and enumerate the services and characteristics of an operator-supplied device ID. TR-005 separately adds product enrollment and read/notification subscriptions on the Devices page.

## Safety boundary

- Diagnostic endpoints never pair, subscribe to notifications, write a GATT value, or send treadmill commands. The product read-only connection can configure a notification descriptor and receive values, but its portable contract has no characteristic-write operation.
- They are diagnostic tools for the trusted local gateway only. Do not expose the Gateway to the internet.
- The physical safety key and treadmill console remain authoritative. These tools cannot start, pause, stop, or change the belt.

## Start the Gateway

Run the gateway on the Windows VM, then use a separate PowerShell window:

```powershell
.\eng\ble-diagnostics.ps1 -Action Scan -DurationSeconds 5
```

`DurationSeconds` accepts only 1 through 30. The response contains deduplicated devices in deterministic device-ID order. If duplicate advertisements are observed, the strongest known RSSI is retained and advertised service UUIDs are combined.

To enumerate an observed device without using a cached result:

```powershell
.\eng\ble-diagnostics.ps1 -Action Gatt -DeviceId '<device-id-from-scan>'
```

GATT enumeration is bounded to 15 seconds. It opens a read-only connection, requests uncached services and characteristics through the Windows adapter, then disposes the connection. A `503` indicates the adapter is unavailable; a `504` means the bounded enumeration timed out.

## HTTP endpoints

| Method | Path | Bound | Result |
|---|---|---|---|
| `GET` | `/api/diagnostics/ble/scan?durationSeconds=5` | 1–30 seconds | Deduplicated passive advertisements; `Cache-Control: no-store` |
| `GET` | `/api/diagnostics/ble/devices/{deviceId}/gatt` | Device ID 1–256 characters; 15 seconds | Uncached service and characteristic metadata; `Cache-Control: no-store` |

Both endpoints return `400` for invalid input. They have no request method or payload capable of pairing, subscribing, writing, or controlling hardware.

## Product enrollment and telemetry

Open `/devices` to perform a bounded five-second scan, then enroll one supported treadmill and one Heart Rate Service device. Treadmill enrollment requires an explicit `Ftms` or `Vendor` telemetry choice. The gateway never silently combines or switches these paths. Enrollment mutations are rejected while a workout is armed, running, or waiting for physical resume.

The exact Omega Z may omit its local name while still advertising Cycling Speed and Cadence `1816` plus FTMS `1826`. That unnamed, dual-service signature is accepted only as an Omega read-only enrollment candidate. A named non-Omega device is not matched from those services alone. The raw advertised name is carried separately from the display label, and exact model/firmware still come from Device Information reads after connection. This candidate match does not enable any control capability.

The product coordinator reconnects each role independently with a bounded one-to-thirty-second delay. Every connection attempt gets a new generation; the UI reports its state, last sample freshness, passively observed evidence, and a sanitized persistent fault. Forgetting a device cancels its worker and archives the enrollment.

## Product command boundary

TR-006B adds a separate command-only Windows connection used solely by the serialized treadmill command coordinator. The diagnostics and enrollment APIs still cannot obtain it. A Start/Stop request is rejected unless the persisted exact model and firmware have `HardwareVerified` evidence and the corresponding capability, FTMS is explicitly selected, telemetry is fresh, the lease/session/version/generation match, and Start sees a stopped belt. The coordinator requests FTMS control, sends one motion operation, and requires both the matching response and fresh measured telemetry. It never retries or restores Start.

This implementation does not itself authorize a hardware trial. Use the dedicated story gate and sanitized evidence procedure; the Devices screen remains read-only.

## FTMS command troubleshooting

Use the structured disposition and the last successful boundary to distinguish failures. Never
reuse an operation ID, and never retry an `Unknown` motion outcome.

| Symptom | Meaning | Operator/developer action |
|---|---|---|
| `AccessDenied` while enumerating `1826` characteristics | WinRT service-detail discovery or competing handle problem; no control write occurred if the stack ends in discovery | Confirm no other gateway owns the unit. Keep targeted discovery on one handle and yield after service discovery. Do not change Windows privacy settings when Bluetooth consent already reports `Allow`. |
| `SharingViolation` from `OpenAsync` | Telemetry and command service handles use incompatible sharing modes; no command write occurred | Both paths must open FTMS with `SharedReadAndWrite`. Stop the old process so stale WinRT handles are released before a new attempt. |
| `WindowsBleResponseTimeoutException` during Request Control | The GATT write completed, but this firmware returned no Request Control notification | The exact Omega profile may continue after the bounded `300 ms` control-response window. Motion commands still require their own response and telemetry confirmation. |
| Timeout/invalid response after a Start, Stop, speed, incline, or Pause write | Physical outcome is `Unknown` | Suspend automation, inspect the belt, use physical Stop if needed, and do not retry or replay. |
| Confirmed response but no fresh matching telemetry | Physical outcome is `Unknown` | Treat the response as insufficient. Check telemetry subscription/generation and use physical Stop if movement is possible. |
| Commands take several seconds despite a running gateway | Control is being reacquired or the belt is ramping/decelerating before telemetry confirmation | Inspect `IssuedAt`, `CompletedAt`, Request Control logs, and the paired latency fields. Consecutive commands on one generation should reuse one command connection/control acquisition. |
| Telemetry generation changes immediately after capability promotion | The coordinator is incorrectly coupling database evidence versions to the physical connection lifecycle | A capability/evidence update must not restart BLE. Only a different enrollment identity may replace the worker; verify with `ReadOnlyDeviceCoordinatorTests`. |

The sanitized exact-device incident and latency values are recorded in
`protocol-evidence/omega-z/2026-08-04-stage3-ftms-start-stop.md`.

## Windows Service Session 0 acceptance gate

Interactive success does **not** prove that a Windows Service can use BLE in Session 0. This remains pending until a separately approved, unattended reboot/service-install acceptance exercise records all of the following:

1. The service starts without an interactive login.
2. A bounded passive scan returns an expected nearby advertisement after reboot.
3. A bounded, uncached GATT enumeration can read only metadata from the approved test device.
4. The operator confirms no pairing prompt, GATT write, notification subscription, or treadmill command occurred.

Do not install a service or reboot the VM as part of this diagnostic procedure. Record the evidence separately once that explicit gate is approved.
