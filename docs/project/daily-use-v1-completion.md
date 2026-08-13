---
title: Daily-use V1 Completion Ledger
type: delivery-status
status: active
owner: project
audience: owner-and-operator
updated: 2026-08-13
---

# Daily-use V1 completion ledger

This ledger is the single distinction between software that is implemented and verified locally, work that still needs an owner-present physical acceptance window, and later integrations that are intentionally outside the current Windows-only release. It does not authorize treadmill commands.

## Product software

| Area | Implemented state | Evidence or remaining validation |
|---|---|---|
| Profiles and HR zones | Two-runner profiles, editable maximum HR, generated Z1–Z5 defaults, manual zone editing, controller steps/cooldowns | Core/integration/browser suites and populated profile screenshots |
| Workouts | Searchable immutable workout library/editor/import, fixed/ramp/HR targets, repeat blocks, and capability-aware requested/normalized/rejected preflight | Planning, capability-policy, integration, and populated editor/import evidence |
| Training plans | Ordered exact-revision plans with 5K/10K/Base/Custom categories, one active run per runner, restart, archive, progress, calendar-first recommendation, and phase/week render-on-expand behavior for the 174-session plan | Domain, SQLite, HTTP replay, touch UI, populated gallery, and bounded browser DOM evidence |
| Daily reuse and generated sets | Per-runner recent structured runs can be selected in one tap; verified treadmill-workout v4 bundles import atomically as exact-revision plans with one selected variant per slot | TR-017 parser, transaction, endpoint, browser, and populated gallery evidence |
| Run and Control | One Run flow selects calendar, plan, library, or manual; preparation routes to a minimal full-screen control surface; Stop/Pause remain directly reachable; active runs request a screen wake lock with safe fallback | Browser viewports at 440×956, 1180×820, and desktop; no physical command is implied by simulator proof |
| Heart rate | Polar-first multi-sensor roster, Garmin HR-broadcast fallback, visible missing/strap/watch state, live BPM, optional battery, freshness, bounded automatic reconnection, sanitized reliability history, and automation suspension rules | Software validated with fake transport/time and browser fixtures; optional real battery availability and one ordinary reconnect remain owner observations |
| HR-speed automation | Shadow, decrease-only, and two-way modes with profile limits, dwell/cooldowns, hardware increment alignment, stale-data suspension, and no blind retry | Fake-time unit tests plus a coordinator/API simulation that decreases, increases, suspends on stale HR, and stops; final exact-treadmill workout remains external |
| History and recovery | Session history/detail, 100-card render windows, 500-row server paging, CSV/FIT Activity export, bounded diagnostics, versioned `.trb` backup, previewed maintenance-locked transactional restore, startup/daily database checks, reversible SQLite maintenance, verified last-known-good backups, full-schema semantic verification, and rollback | Automated round trips, corrupt-database checks, and a 1,095-session/three-year scale fixture pass; clean-install operator recovery remains external |
| Runtime diagnostics | Independent live, ready, and read-only BLE health surfaces; treadmill power-off degrades BLE only; bounded correlation IDs and normalized route telemetry are exposed read-only | TestServer evidence; Session 0 BLE proof remains external |
| Operator access | Disabled-by-default optional passphrase gate; anonymous reads, bearer-protected API mutations, bounded in-memory sessions, tab-scoped browser storage, and throttled failures | Endpoint and browser-client contracts pass; trusted private LAN remains mandatory |
| Updates | Operations can check, verify/stage, and activate a signed package; helper backs up, migrates, health-checks, and rolls back | UI-driven A→B and broken-C rollback are documented; signed 1.5.8 is installed in the protected stable feed and the running 1.5.6 service reports it as `Available`; UI staging and activation remain operator actions |

## Owner-present physical acceptance still required

These are acceptance observations, not missing application code. They must not be simulated or inferred:

- simultaneous Omega Z S3.02 plus Polar H10 telemetry for a bounded 5–10 minute observation, followed by one successful treadmill power-cycle/reconnect check;
- exact-device Pause plus planned-transition/manual-override trials through the production browser. Stage 3 already proves Start, Stop, speed 1.2/1.5/1.0, incline 1.0/0.5, and final measured speed/incline 0.0 on S3.02/V10.23.17;
- a representative HR-driven workout covering decrease, increase, manual suspension, explicit re-enable, stale HR, and safe Stop;
- Windows Service Session 0 scan/enrollment/subscription recovery before login after a planned reboot;
- final clean-install restore and the next signed UI update from the protected feed.

The installed Windows service was verified read-only as healthy on `1.5.41` on 2026-08-13. The hardening changes in this source pass are not installed or activated; they must not be described as deployed until a separately authorized signed release and activation completes.

No remote Start, Stop, Pause, speed, or incline command may be sent merely to complete this ledger. The physical console, safety key, and physical Stop remain authoritative, and disconnect is never a stop mechanism.

## Requested later integrations

| Integration | Current truthful state | Next story boundary |
|---|---|---|
| Garmin watch HR | Implemented through standard BLE HR Broadcast; the watch must enable broadcasting | Owner-present Fenix 8/Vivoactive pairing and fallback proof |
| Garmin Connect automatic upload | Requested; not implemented | Establish an officially supported Garmin API/account path before credentials or uploads are added; FIT Activity download remains the working manual path |
| Apple Health | Requested; not implemented on the Windows gateway | Requires a HealthKit-capable iPhone companion or another Apple-approved bridge; the web app cannot write HealthKit directly |
| Garmin-native activity details | Available through the watch | A native Treadmill activity recorded on the watch remains the most reliable automatic Garmin Connect route; compare it with exported FIT after hardware acceptance |

## Known software boundary

FTMS is the only hardware-accepted control mode. The independently authored Omega vendor encoder remains fixture-tested research, but the product does not expose vendor motion writes because the exact S3.02/V10.23.17 write characteristic/response/duplicate-command behavior has not been accepted. There is no automatic FTMS/vendor fallback or mixed-mode write path.

The repository has a Git baseline and local-only tagged release governance. Working-tree changes still require intentional review and commit before the release workstation validates, builds, signs, packages, tags, and uploads a release. GitHub Actions is disabled.

## Release operator path

Normal application updates never replace the privileged helper or trust anchor. The operator signs and publishes an immutable higher version, installs the same-key package into the administrator-protected stable feed, then uses **Operations → Check now → Verify and stage → Activate staged update**. The service performs the backup, migration, promotion, health check, and rollback. Full commands, ACL boundaries, states, and recovery are in [Release operations](release-operations.md).
