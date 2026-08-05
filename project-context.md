# TreadmillRunner project context

TreadmillRunner is a .NET 10 Windows-local gateway and browser UI for one Horizon Omega Z. Start with [the reviewed project package](docs/project/project-context.md), [the decisions](docs/project/decision-record.md), and [the story backlog](docs/project/backlog.md).

## Daily commands

```powershell
.\eng\bootstrap.ps1
.\eng\run-simulator.ps1
.\eng\validate.ps1
```

## Architectural coordinates

- Portable domain and public contracts: `src/TreadmillRunner.Core`
- Independently authored device/file protocols: `src/TreadmillRunner.Protocols`
- Treadmill extension seam: portable `ITreadmillProtocol` implementations resolved by `TreadmillProtocolRegistry`; Omega Z ships first and future Domyos support adds an adapter rather than branching the session engine
- Windows BLE, EF Core/SQLite migrations, online backup, and updates: `src/TreadmillRunner.Infrastructure`
- ASP.NET Core Windows host and feature slices: `src/TreadmillRunner.Gateway`
- Browser-only Blazor WebAssembly UI: `src/TreadmillRunner.Web`
- Unit/integration/browser tests: `tests`
- Project-owned automation: `eng`
- Read-only Windows BLE operator procedure: [`docs/project/windows-ble-operations.md`](docs/project/windows-ble-operations.md)
- Append-only AI harness findings: [`docs/project/ai-harness-findings.md`](docs/project/ai-harness-findings.md)
- Project task responsibilities and ordered Codex/Copilot/Claude model fallbacks: [`.agents/orchestration.json`](.agents/orchestration.json); portable rules: [`orchestration.md`](orchestration.md)
- Planning data model and import flow: [`docs/project/planning-data.md`](docs/project/planning-data.md)
- Authoritative simulated runner flow and recovery: [`docs/project/live-session.md`](docs/project/live-session.md)
- End-user Windows installation and updates: [`docs/installation.md`](docs/installation.md)
- Signed GitHub/local release operation: [`docs/project/release-operations.md`](docs/project/release-operations.md)

## Planning data and operations

- TR-003 provides local profiles, immutable canonical workout revisions, preview-first native JSON/QDomyos XML/Garmin FIT imports, and recurring calendar alternatives and exceptions.
- TR-013 provides a separately labelled, disabled-by-default, per-profile unsupported Garmin completed-FIT uploader and a public-API Connect IQ companion source/store package. Upload uncertainty is terminal and never blindly retried; watch recording requires explicit Select and never controls the treadmill. SDK/simulator/physical-watch/signing/IQ Store, trusted-HTTPS, and live-account acceptance remain external release steps.
- TR-014 provides the populated phone/tablet/desktop screenshot baseline plus bounded live-chart and simulator-backed mobile control reliability acceptance.
- TR-015 adds GitHub Releases as the signed primary update transport, keeps the protected local folder as fallback, accepts only bounded pinned-key offline bundles in Operations, and provides public MIT repository/install/release hygiene.
- TR-016 makes `vMAJOR.MINOR.PATCH` tags the only automatic GitHub Actions trigger, retains deliberate manual validation, and keeps build signing/publication on the local non-exportable signer through `eng/create-github-release.ps1`. Ordinary pushes and pull requests consume no Actions run.
- EF Core migrations are committed under `src/TreadmillRunner.Infrastructure/Persistence/Migrations`; production startup neither calls `EnsureCreated` nor silently applies migrations.
- Use [`eng/database.ps1`](eng/database.ps1) for migration status, reviewed migration creation, SQL-script generation, and applying migrations to an explicit SQLite file. The simulator passes the absolute equivalent of `.\data\treadmillrunner.db` to the gateway through `Persistence__DatabasePath`; startup does not migrate it. SQLite runs with foreign keys and WAL; the online-backup proof restores into a separate database, not over a live one.
- Release WebAssembly publish work may require [`eng/clean-wasm-publish.ps1`](eng/clean-wasm-publish.ps1) before publishing: `dotnet clean` can leave stale generated WebCIL output.
- Never create or move release tags manually. The canonical tagged GitHub release and recovery procedure is [`docs/project/release-operations.md`](docs/project/release-operations.md).

## Live session and history

- TR-004 implements a gateway-owned simulator session at 4 Hz, one-second SQLite samples, immediate event persistence, a 5-second/15-second controller lease, reload reclaim, debrief, session analytics, and weekly completed totals.
- TR-005 implements exactly-one-role device enrollment, explicit FTMS/vendor read-only Omega telemetry, simultaneous Polar H10 subscription, connection generations/freshness, and composition into the same authoritative live coordinator. Exact-device Stage 1/2, power-cycle, soak, and Session 0 observations remain external gates.
- The live and historical charts are derived from bounded transient or persisted values; they are not static presentation fixtures.
- Startup interrupts unfinished persisted sessions. Browser loss alone does not stop the session. The Start/Stop API accepts only volatile, single-use intents for an exact model/firmware with persisted hardware-verified capability; current hardware remains capability-disabled until its dedicated physical gate passes.
- Accelerated four-hour cadence/bounded-memory proof, a 14,400-write SQLite soak, and local loopback p95 targets pass. Normal household Wi-Fi remains a deployment acceptance check. Auto-update, real BLE session operation, and hardware commands are not implemented by TR-004.

## Safety reminder

Remote belt Start is software-complete but capability-disabled for current hardware. It is exposed only after the model/firmware-specific TR-006B gate; reconnect, reload, restart, update, and retry may never replay Start or resume a workout. Hardware capabilities remain disabled until their individual evidence gates pass.
