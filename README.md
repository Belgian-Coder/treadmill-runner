# TreadmillRunner

TreadmillRunner is a local-first treadmill workout application for one Horizon Omega Z. A .NET 10 gateway runs on the existing Windows 11 VM, owns Bluetooth and workout state, and serves an install-free touch interface to browsers on the home network.

The project combines a deterministic simulator with one exact Horizon Omega Z hardware adapter. On console software `S3.02` and Bluetooth firmware `V10.23.17`, owner-observed Stage 3 evidence verifies FTMS Start, Stop, speed, and incline with response-plus-fresh-telemetry confirmation. Pause remains disabled because it is not hardware verified. The treadmill safety key, physical Stop, and console remain authoritative.

## See it in use

| Live control and recovery | Premade training plans |
|---|---|
| [![Recovered live Control dashboard](screenshots/showcase/tr-023-control-recovered-desktop.png)](screenshots/showcase/tr-023-control-recovered-desktop.png) | [![Premade plan catalog and compatibility preview](screenshots/showcase/tr-024-premade-plan-catalog.png)](screenshots/showcase/tr-024-premade-plan-catalog.png) |

The gateway owns active workout timing if a browser disappears. Browser controls reconnect indefinitely, while BLE and service-restart recovery stay guarded: recovery never sends Start, never replays an uncertain command, and never silently overrides an apparent physical-console change. See the [live-session guide](docs/project/live-session.md) for the exact guarantees.

## Install on Windows

For normal use, download `TreadmillRunner-<version>-Windows-x64.zip` from [GitHub Releases](https://github.com/belgian-coder/treadmill-runner/releases/latest), extract it, and run `Install-TreadmillRunner.cmd`. The installer asks for administrator approval, installs the Windows service, verifies readiness, and opens the dashboard. The NUC must run Windows 11 x64 on a **Private** household network with the [ASP.NET Core Runtime 10 x64](https://dotnet.microsoft.com/download/dotnet/10.0).

See the [complete installation guide](docs/installation.md) for first-run enrollment, phone access, online GitHub updates, the protected local-feed fallback, signed offline ZIP updates, repair, and data retention. Never port-forward the dashboard or expose it to an untrusted network.

## Current status

- Story TR-001 is complete: the solution, simulator, live SignalR path, responsive Blazor UI, tests, scripts, and Windows-first documentation are in place.
- Story TR-003 is complete: local household profiles, immutable workout revisions, preview-first imports, and a recurring training calendar are backed by EF Core/SQLite.
- Story TR-004 is implemented through the daily simulated run, controller reclaim, one-second history, RPE/note debrief, data-derived charts, HR-zone/adherence analytics, weekly totals, sound preference, and responsive Playwright coverage. Accelerated four-hour cadence/memory proof, a 14,400-write SQLite soak, and loopback p95 targets pass; deployment evidence over normal household Wi-Fi remains pending.
- Story TR-005 is implemented through local Omega Z/Polar H10 enrollment, structurally read-only BLE reads/subscriptions, explicit FTMS or Omega-vendor telemetry, simultaneous connection/freshness state, and real telemetry composition into the authoritative session engine. Ten power cycles and the 60–90 minute simultaneous read-only soak remain owner-present acceptance.
- Story TR-006B/C includes portable FTMS control codecs, an isolated serialized non-replayable command connection, operation/lease/session/generation guards, response-plus-fresh-telemetry confirmation, explicit Confirmed/Rejected/Unknown results, Hold-to-start, and the accepted exact-firmware Stage 3 sequence. Pause and vendor motion writes remain disabled pending their separate exact-device gates.
- Story TR-003B adds persisted ordered 5K/10K/Base/Custom training programs with runner-specific completed-only progression and calendar-first next-run selection.
- Story TR-009 closes deterministic completion gaps: capability-aware target preflight, immutable treadmill session snapshots, Polar-first selection, system-level simulated HR automation, screen wake lock, `/health/ble`, and maintenance-locked semantic restore verification.
- TR-002's read-only Windows adapter can passively scan and perform uncached GATT enumeration. An interactive VM test found nearby advertisers and successfully enumerated a connectable device without pairing, subscribing, or writing.
- Windows Service Session 0 behavior and exact Omega/Polar simultaneous hardware acceptance remain external gates.
- No unverified command is enabled, no Unknown outcome is retried, and reconnect expires every pending intent.
- TR-013 adds separately labelled, disabled-by-default per-profile Garmin FIT upload through a pinned unsupported adapter, with durable duplicate-safe jobs and explicit recovery. Its Connect IQ companion source supports explicit native watch recording and profile/session status for Fenix 8 and Vivoactive 5/6; SDK/simulator/physical-watch/store acceptance remains an external release step.

## Developer prerequisites

- Windows 11
- .NET SDK 10.0.110 or the patch selected by `global.json`
- PowerShell 7 or Windows PowerShell 5.1
- Python 3.12 for the deterministic validation harness
- Node.js/npm for Playwright browser validation

## Developer quick start

```powershell
.\eng\bootstrap.ps1
.\eng\database.ps1 -Action Update -DatabasePath .\data\treadmillrunner.db
.\eng\run-simulator.ps1
```

The simulator sets `Persistence__DatabasePath` to the absolute equivalent of
`.\data\treadmillrunner.db` before launching the gateway. It does not create or migrate the
database at startup, so the explicit `database.ps1` update above is required for a fresh checkout.
Open `http://localhost:5180`. Run all deterministic checks with:

```powershell
.\eng\validate.ps1
```

Use the Devices page to run a bounded passive scan and enroll one treadmill and one primary heart-rate monitor. Choose FTMS or the Omega vendor telemetry path explicitly; the gateway does not silently switch between them. Enrollment and diagnostics remain read-only, and simulator mutation endpoints exist only in Development.

Some Omega Z firmware advertisements omit the local name. TreadmillRunner accepts the observed unnamed `1816` plus `1826` signature only as a read-only Omega enrollment candidate, then obtains model and firmware from Device Information. Named non-Omega devices are not matched by this fallback, and it never enables controls.

## Planning your workouts

Use the navigation in the simulator UI to create or select a local profile, build a workout, import one, and plan it on the calendar. A saved workout is an immutable revision: editing creates a new revision, so existing calendar choices continue to reference the exact workout that was selected.

Imports are previewed before anything is saved. The supported import paths are native workout JSON, QDomyos XML, and Garmin FIT workouts. Confirming a preview rechecks the original bounded file; QDomyos files that do not state units require an explicit unit choice.

The calendar supports weekly schedules, alternatives for a day, and skip/add/replace exceptions. Your active profile is local to the browser; a selected calendar alternative is persisted for that profile and date.

**Workouts → Premade plans** offers 16 profile-scoped 5K, 10K, general-fitness, walking, maintenance, and heart-rate templates. Preview checks runner and treadmill limits before **Add to my training** creates an inactive immutable copy. The 58-week plan remains readable through phase/week groups rather than a flat 174-row editor. See [Premade training plans](docs/project/premade-plans.md).

## Running in the simulator

On the Today page, select a runner and workout, review readiness, take control, and arm the session. Arming never starts a belt. The simulator test action represents measured physical movement; the gateway then owns progression and continues if the browser reloads or disconnects. After completion, save an optional RPE score and note, then open History for persisted charts, zone time, adherence, events, and weekly totals.

See [the live-session guide](docs/project/live-session.md) for contracts, recovery behavior, and current limitations. The Windows service supports signed check, verify/stage, explicit UI activation, health verification, and automatic rollback. Use the [release operations runbook](docs/project/release-operations.md) to publish, update, rotate trust, or recover a feed.

## Database operations

SQLite uses reviewed EF migrations and WAL. Application startup does not create or migrate a production database automatically. The repo-local development database is `.\data\treadmillrunner.db`, which is the same explicit path `run-simulator.ps1` passes to the gateway. Use the project-owned helper to inspect migrations, create a migration script, or apply reviewed migrations to that path:

```powershell
.\eng\database.ps1 -Action Status
.\eng\database.ps1 -Action Script
.\eng\database.ps1 -Action Update -DatabasePath .\data\treadmillrunner.db
```

The `Script` action writes `artifacts/database/treadmillrunner.sql` by default. The TR-003 backup proof uses SQLite's online backup API and opens the backup as a separate database; replacing a live user database is deferred to TR-007.

For a Release WebAssembly publish, run `.\eng\clean-wasm-publish.ps1 -Configuration Release` first when stale WebCIL output is possible. `dotnet clean` can retain generated WebCIL assets.

## Read next

1. [Project context](project-context.md)
2. [Story backlog](docs/project/backlog.md)
3. [Architecture](docs/project/architecture.md)
4. [Safety rules](docs/project/safety-guidelines.md)
5. [Omega Z evidence](docs/project/omega-z-protocol-findings.md)
6. [Implementation plan](docs/project/implementation-plan.md)
7. [Windows BLE read-only operations](docs/project/windows-ble-operations.md)
8. [AI harness findings](docs/project/ai-harness-findings.md)
9. [Simulated live session](docs/project/live-session.md)
10. [Release operations](docs/project/release-operations.md)
11. [Garmin integrations](docs/project/garmin-connect.md)
12. [Connect IQ companion and IQ Store release](docs/project/connect-iq-companion.md)
13. [Premade training plans](docs/project/premade-plans.md)

## License

TreadmillRunner is available under the [MIT License](LICENSE).

The sibling `../qdomyos-zwift` checkout is research evidence only and is not part of this repository.

## Maintainer releases

GitHub Actions is disabled in repository settings, and no workflow or Dependabot update configuration is committed: commits, pull requests, and tags never start a hosted build. Releases are validated, built, signed, packaged, tagged, and uploaded from the release workstation because the signing key is non-exportable and hosted minutes are intentionally not used:

```powershell
.\eng\create-github-release.ps1 `
  -Version 1.5.10 `
  -ReleaseNotes 'Describe the user-visible changes in this version.'
```

Do not create or move tags manually. See [release operations](docs/project/release-operations.md) for prerequisites, assets, interruption recovery, and update activation.
