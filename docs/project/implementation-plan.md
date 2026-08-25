---
title: TreadmillRunner Implementation Plan
type: implementation-plan
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-25
---

# Step-by-step implementation plan

The authoritative sequence is the [story backlog](backlog.md). Each story is created and finished through the project workflow with acceptance mapping, red/green tests, deterministic validation, documentation delta, and explicit skipped hardware evidence.

## US-TR-041 coordinated improvement program

The current implementation slice preserves the completed Lighthouse shell/startup/performance work and adds the following software capabilities. Device telemetry now carries source, contact, quality, observation time, and freshness; speed and incline age independently; omitted or implausible values cannot drive automation or command confirmation; generic FTMS remains read-only. Live-session transitions release locks before generation-guarded persistence/fan-out effects, one-second checkpoint writes are serialized, browser delivery is latest-only, operation receipts are session-scoped with 90-day terminal retention, and BLE reconnect/readiness/integrity work follows the bounded operational policies.

Product surfaces include terminal debrief editing, profile-scoped run-experience preferences, weekly/monthly distance-duration-run-count goals with progress, Metric-only profile contracts, TCX Activity/native JSON/FIT Workout exports, Garmin review-required reconciliation evidence, an explicit setup-only Training API state, WalkingPad source/hash/regeneration provenance, reusable typed/profile/chart UI components, and a complete 180×180 Apple touch icon. The current migration normalizes profile units, reconciles pre-existing active-session conflicts, enforces active-session uniqueness, and adds lease/recovery/receipt indexes.

The program does not add sensitive-data boundaries, an active-session runner-identity lock, live workout or calendar-completion guidance, alternate unit modes or conversions, optional watch/health ecosystems, backup-destination configuration, release/deployment/installation, or physical-equipment acceptance. The approved Garmin Training API boundary remains disabled without an approved adapter, contract, and credentials; proprietary payloads are never guessed. See [walkingpad-plan-provenance](walkingpad-plan-provenance.md), [Garmin integrations](garmin-connect.md), and [live-session](live-session.md) for the detailed contracts and limits.

## TR-042 idle Home live-transport lazy loading

Move ownership of the existing SignalR lazy-assembly group from route selection into the gateway connection supervisor. Idle Home performs only the existing read-only active-session lookup; active recovery, direct Control navigation, and an explicit Prepare action load the transport on demand. The first lease request waits on the bounded initial connection task without retrying or replaying commands. Measure the Home request reduction and affected Lighthouse scores, then release and roll out only after the canonical release gate and an idle installed-service check.

## TR-001: foundation and simulator

Create the five .NET 10 production projects, portable contracts and Omega/HR parsers, simulated treadmill/HR source, ASP.NET Core gateway, SignalR live snapshots, responsive Blazor WebAssembly dashboard, unit/integration tests, deterministic scripts, harness modules, and Windows-first context. No real BLE write path exists.

## TR-002: read-only Windows BLE feasibility

Implement advertisement scanning, identity diagnostics, uncached GATT enumeration, bounded event channels, and health states through the RZ616. Run interactively and as a Windows Service before login. Failure of Session 0 BLE reopens the gateway platform decision; automatic login is not an accepted workaround.

## TR-003: local household planning data

Add reviewed EF Core SQLite migrations, profiles, immutable workout revisions, touch editor, native/FIT/QDomyos import preview, weekly recurrence, exceptions, alternative sessions, and online backup/restore. Persistence tests use realistic SQLite boundaries.

## TR-004: complete simulated UX

Complete controller lease/observers, browser-loss continuation, live planned/requested/measured charts, history, sound, accessibility, three-viewport Playwright screenshots, and latency/soak evidence.

## TR-005: physical read-only validation

Enroll the exact Omega, Polar H10, and compatible Garmin broadcaster. Capture advertisements/GATT/notifications, verify vendor and FTMS telemetry, simultaneous connections during a bounded 5–10 minute observation, one successful power-cycle/reconnect check, and Windows Service behavior. No physical acceptance test exceeds 10 minutes. Send no control commands.

TR-005A first adds generic FTMS feature and speed/incline range parsing. Protocol-reported support remains distinct from hardware-verified control, so the same contracts can be reused by Omega Z and future Domyos adapters.

## TR-006: bounded commands and HR speed control

TR-006A/B/C implement generic target preflight, range enforcement, expiring non-replayable command intents, response-plus-telemetry confirmation, Start/Resume, Stop, Pause, speed, incline, planned transitions, manual overrides, and configurable HR-speed automation. Every persisted hardware capability remains false until the corresponding exact-device command is confirmed during the single fixed owner-attended sequence. Reconnect and restart expire outstanding intent and never replay or resume Start.

## TR-007: history/export/operations

CSV and FIT Activity export, full SQLite backup, bounded diagnostics, restore preview/confirmation, transactional restore with rollback, signed local/UNC update staging, idle activation, migration backup, helper-based service promotion, health verification, and automatic rollback are implemented. The remaining work is deployment evidence: clean-install restore, real A→B promotion, and broken-C rollback on the Windows VM.

## TR-008: optional integrations

Research only officially supported Garmin upload. Add Domyos or other treadmill adapters only from independent fixtures without changing Core/UI behavior. Run 500 and Challenge Run have separate evidence stories because a vendor name is not a protocol fingerprint.

## Required commands

```powershell
.\eng\bootstrap.ps1
.\eng\build.ps1 -Configuration Release
.\eng\test.ps1 -Configuration Release
.\eng\validate.ps1
```

Hardware tests are separate explicit scripts/checklists and can never be inferred from a successful simulator build.
