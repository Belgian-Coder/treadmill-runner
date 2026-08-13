---
title: Operational Hardening and Performance Acceptance
type: operator-and-developer-guide
status: active
owner: project
audience: operator-and-developer
updated: 2026-08-13
---

# Operational hardening and performance acceptance

This pass turns the remaining performance, recovery, observability, and optional-access recommendations into repeatable product contracts. It does not authorize a treadmill command, service restart, update activation, release, or installation.

## Safe physical preflight

Run the GET-only preflight against an already-running gateway:

```powershell
.\eng\physical-acceptance-preflight.ps1 -BaseUri http://127.0.0.1:5180
```

The script reads readiness, BLE health, live-session state, enrollment, and device status. It verifies the exact Omega Z model/firmware and Polar H10 enrollment without scanning, connecting, disconnecting, taking a lease, creating a session, or issuing a command. Add `-RequireFreshTelemetry` only during an owner-present observation window. A pass without that switch proves deterministic prerequisites, not live physical acceptance.

## Recovery proof

Run the isolated recovery contract without touching the installed service or production data:

```powershell
.\eng\verify-recovery-acceptance.ps1
```

It covers update rollback, transactional SQLite restore, backup round trips, and release-script contracts in temporary test data. Clean-install restoration remains an operator acceptance action because it changes an installed environment.

## Operational telemetry

Every gateway response carries a bounded `X-Correlation-ID`. A valid caller-supplied value is retained; invalid or oversized values are replaced. `GET /api/operations/telemetry` returns an in-memory route summary with counts, failures, average/maximum duration, last status, and last observation. Route keys replace GUID/numeric identifiers, exclude query strings, cap path depth, and are limited to 64 entries. The same observations are emitted through `System.Diagnostics.Metrics` for a future OpenTelemetry exporter without requiring one now.

## Optional operator access

Operator access is disabled by default. When enabled, reads remain anonymous and every non-GET/HEAD/OPTIONS `/api` request requires a short-lived opaque bearer token. Tokens are held in bounded gateway memory and browser `sessionStorage`; closing the tab removes the browser copy. There is no authentication cookie and therefore no ambient cookie/CSRF authority. Login failures are bounded per peer.

Generate a PBKDF2 hash in Windows PowerShell 5.1 or PowerShell 7, then place only the result in protected service configuration:

```powershell
$passphrase = Read-Host 'Operator passphrase' -AsSecureString
.\eng\new-operator-access-secret.ps1 -Passphrase $passphrase
```

```text
OperatorAccess__Enabled=true
OperatorAccess__SecretHash=pbkdf2-sha256$210000$<salt>$<hash>
OperatorAccess__SessionMinutes=30
OperatorAccess__MaximumFailedAttempts=5
OperatorAccess__FailureWindowMinutes=5
```

Do not commit the hash or passphrase. To roll back this boundary, set `OperatorAccess__Enabled=false` and restart in an approved maintenance window; the default behavior is unchanged.

## Browser and data budgets

- The Operations feature is a lazy WebAssembly assembly. History and other read-only routes do not download it.
- The server-rendered, noninteractive Operations boot shell requests the preferred local QR code immediately, before WebAssembly becomes interactive.
- The live SignalR closure remains lazy and route-owned by Run and Control.
- Premade and installed plans render phase/week session rows only after the user expands that group. Flat plans render 24 rows at a time.
- History reads remain server-bounded at 500 rows per request and render 100 cards at a time. Detail charts remain capped at 240 representative samples while export and analytics keep full fidelity.
- Browser acceptance caps History at 1,000 DOM nodes and Operations at 1,200, requires History readiness within 20 seconds on the test host, and proves the Operations assembly is absent before that route is opened.
- SQLite acceptance seeds 1,095 daily sessions, requires newest-first 500-row history in under five seconds, verifies detail retrieval, and caps the fixture database at 25 MB.

## Evidence and recovery notes

Focused deterministic acceptance passed for release scripts, telemetry/correlation, operator access, three-year history, and route/DOM budgets. The first focused browser attempt exceeded the command wrapper while the trimmed gateway publish was still running; the publish completed successfully, the harness reused that verified output, and the focused browser test then passed. This was a wrapper-duration failure, not a product assertion or permission to weaken the budget.

The required single full Release gate reached 290 of 293 passing integration tests before exposing an extracted Garmin configuration-key regression and stopping before its browser phase. The registration was corrected to use `GarminConnect:Provider`; the three failed Garmin tests then passed 3/3. A subsequent independent browser run found that an empty current Agenda week hid the first upcoming scheduled week; the default-open rule was corrected, its focused scenario passed, and the complete browser suite passed 109/109 functional plus 1/1 performance tests. The full gate was deliberately not repeated because the repository workflow permits exactly one final full invocation; the combined corrective evidence is retained instead of claiming that the one-shot command itself passed.

If the lazy Operations route fails, restore the prior Web project layout and remove the `BlazorWebAssemblyLazyLoad` entry as one source change; no database or installed state is involved. If operator access is misconfigured while enabled, startup validation fails closed. Remove or correct the protected environment values before restarting. Update/restore rollback continues to use the existing signed-update and verified-backup boundaries.

Live Omega/Polar telemetry, power-cycle recovery, Session 0 after reboot, representative HR automation, clean-install restore, and the next signed UI activation remain external owner-present gates.
