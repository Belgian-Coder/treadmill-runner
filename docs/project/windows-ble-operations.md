---
title: Windows BLE Read-Only Operations
type: operations-guide
status: active
owner: project
audience: operator-and-developer
updated: 2026-08-29
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
| `GET` | `/api/diagnostics/ble/scan?durationSeconds=5` | 1–30 seconds | Deduplicated active read-only advertisements; `Cache-Control: no-store` |
| `GET` | `/api/diagnostics/ble/devices/{deviceId}/gatt` | Device ID 1–256 characters; 15 seconds | Uncached service and characteristic metadata; `Cache-Control: no-store` |

Both endpoints return `400` for invalid input. They have no request method or payload capable of pairing, subscribing, writing, or controlling hardware.

## Product enrollment and telemetry

Open `/devices` to perform a bounded five-second scan, then enroll one supported treadmill and one or more Heart Rate Service devices with profile-specific assignments. Treadmill enrollment requires an explicit `Ftms` or `Vendor` telemetry choice. The gateway never silently combines or switches these paths. Enrollment mutations are rejected while a workout is armed, running, or waiting for physical resume.

The exact Omega Z may omit its local name while still advertising Cycling Speed and Cadence `1816` plus FTMS `1826`. That unnamed, dual-service signature is accepted only as an Omega read-only enrollment candidate. A named non-Omega device is not matched from those services alone. The raw advertised name is carried separately from the display label, and exact model/firmware still come from Device Information reads after connection. This candidate match does not enable any control capability.

The product coordinator keeps enrolled devices disconnected while the household is idle. Selecting a runner and workout starts a two-minute preparation demand for the enrolled treadmill and the runner's applicable heart-rate sensors. Arming the session holds those read-only telemetry connections until the session reaches a terminal End, completion, reset, or interruption. The stop-backed Pause remains resumable and therefore retains the connections. Terminal cleanup also closes the separate FTMS command connection so the treadmill is not kept awake by either path. A generic FTMS enrollment can expose standard distance, energy, heart rate, elapsed/remaining time, power, speed, and incline fields for read-only status; it never grants model-independent control.

While an active session or persistent manual **Connect** request has connection demand, each role reconnects independently after 1, 2, 4, 8, then at most 10 seconds. A manual request remains active until **Disconnect** or gateway restart. Idle demand backs off to five minutes. If Windows can no longer open an unpaired enrolled treadmill address from its system cache, the worker runs a cancellable five-second active read-only advertisement watch and retries immediately only when that exact stored address is observed. Treadmill identity and command authority never rebind.

Automatic run demand starts the runner's preferred heart-rate source first and progressively adds the next assigned fallback only while an earlier source is failing or stale; a recovered preferred source releases the fallback worker. Every demanded heart-rate worker starts with a fresh bounded active scan, asking Windows for scan-response metadata and following the standard BLE Heart Rate Service discovery model used by Polar and Garmin HR Broadcast rather than treating a historical address as permanent identity. Split advertisement packets are merged by current address, and the observed public/random address type is retained only in a bounded five-minute transport cache. The exact current address wins; otherwise a unique exact display-name match may supply an ephemeral current locator only when the candidate advertised standard HRS or the project's existing Polar advertisement hint. A unique known Polar/Garmin family-and-kind fallback has the same evidence requirement and is allowed only when no other active enrollment has the same family and kind. Duplicate or ambiguous candidates fail closed, and a truncated/overflowed scan disables fallback for that attempt. Fallback never rewrites the stored Bluetooth identity, and GATT must still expose standard `180D` / `2A37` before Ready. Required HR notifications establish Ready before optional Device Information and Battery work begins. After a generic GATT failure the failed locator is excluded from the next bounded scan so an address-rotated source can be selected. Windows uses targeted uncached discovery for Heart Rate, Battery, and Device Information services, so an unrelated protected vendor service cannot block HRS. The worker's 15-second waiter requests cancellation of native discovery/read work; a non-cooperative native call may finish later without pinning the worker. Primary and optional-battery subscriptions use the same watchdog, and iterator disposal is bounded to one second. Teardown detaches handlers and disposes the GATT service/device handles; it does not start a separate CCCD-disable operation against a vanished sensor.

Fresh discovery does not pair, subscribe, issue an application-data GATT write, or send a treadmill command; a peripheral that is off, ambiguous, or not broadcasting remains truthfully unavailable and stays on the normal retry schedule only until demand expires. Active scanning can use more adapter power and remains subject to the existing Windows Service Session 0 platform gate. Every connection attempt gets a new generation; the UI reports its state, last sample freshness, observed evidence, and a sanitized persistent fault. Forgetting a device cancels its worker and archives the enrollment.

Devices exposes concise **Connect** and **Disconnect** actions for every enrollment. **Connect** cancels only that read-only telemetry worker, starts a fresh generation, and creates an idle connection demand that remains until **Disconnect** or gateway restart; selecting it is the idempotent retry path, and it never retries a treadmill command. Disconnect cancels and disposes only the selected read-only connection. Disconnecting the treadmill also closes its retained FTMS command connection without sending a command; disconnecting a heart-rate source cannot affect that treadmill channel. It is rejected while a workout is armed, running, or paused because Bluetooth disconnect is not a treadmill stop mechanism. Local display names can be edited without changing Bluetooth identity or runner assignment; changing name/family metadata briefly restarts only that heart-rate read-only worker so the next fallback uses the new disambiguation immediately. A product-specific rename promotes previously generic `Sensor`/`Other` metadata for status, assignment priority, and reconnect matching, while known watch or chest-strap metadata remains stable across a later friendly rename. The accepted household `OMEGA Z` / `V10.23.17` FTMS profile restores its previously verified Start, Stop, speed, and incline authorization after re-enrollment; the owner may enable or disable those verified controls from the treadmill's advanced settings while idle. This does not authorize any other model/firmware and never enables the raw Pause opcode.

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
| An assigned Polar/Garmin returns after a long absence but **Connect** does not reconnect it | The sensor may not be broadcasting HRS, the fresh scan may be ambiguous, or Windows may still deny targeted GATT access | Confirm Garmin HR Broadcast or the Polar strap is awake. **Connect** gives the worker a two-minute active retry window and a fresh bounded scan on each relevant failure; it never changes the saved assignment. If two matching devices advertise, make their enrolled display identities unambiguous instead of choosing one automatically. |
| Confirmed response but no fresh matching telemetry | Physical outcome is `Unknown` | Treat the response as insufficient. Check telemetry subscription/generation and use physical Stop if movement is possible. |
| Commands take several seconds despite a running gateway | Control is being reacquired or the belt is ramping/decelerating before telemetry confirmation | Inspect `IssuedAt`, `CompletedAt`, Request Control logs, and the paired latency fields. Consecutive commands on one generation should reuse one command connection/control acquisition. |
| Telemetry generation changes immediately after capability promotion | The coordinator is incorrectly coupling database evidence versions to the physical connection lifecycle | A capability/evidence update must not restart BLE. Only a different enrollment identity may replace the worker; verify with `ReadOnlyDeviceCoordinatorTests`. |

The sanitized exact-device incident and latency values are recorded in
`protocol-evidence/omega-z/2026-08-04-stage3-ftms-start-stop.md`.

## Windows Service Session 0 acceptance gate

Interactive success does **not** prove that a Windows Service can use BLE in Session 0. This remains pending until a separately approved, unattended reboot/service-install acceptance exercise records all of the following:

1. The service starts without an interactive login.
2. A bounded active read-only scan returns an expected nearby advertisement after reboot.
3. A bounded, uncached GATT enumeration can read only metadata from the approved test device.
4. The operator confirms no pairing prompt, GATT write, notification subscription, or treadmill command occurred.

Do not install a service or reboot the VM as part of this diagnostic procedure. Record the evidence separately once that explicit gate is approved.
