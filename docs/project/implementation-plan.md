---
title: TreadmillRunner Implementation Plan
type: implementation-plan
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-04
---

# Step-by-step implementation plan

The authoritative sequence is the [story backlog](backlog.md). Each story is created and finished through the project workflow with acceptance mapping, red/green tests, deterministic validation, documentation delta, and explicit skipped hardware evidence.

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
