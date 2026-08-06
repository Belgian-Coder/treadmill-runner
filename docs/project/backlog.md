---
title: TreadmillRunner Story Backlog
type: delivery-backlog
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-06
---

# Story backlog

All implementation stories use the reusable `user-story-workflow`; no project-specific delivery workflow is maintained. Run folders use `US-<story-id>` (for example `US-TR-001`) so the directory is stable while dates remain inside the run files.

| Story | Outcome | State | Hard gate |
|---|---|---|---|
| TR-001 | Repository, harness, Core/Protocol simulator, gateway, responsive live UI, tests, scripts, context | completed | No real BLE writes |
| TR-002 | Read-only Windows BLE scanner, GATT enumeration, Windows Service Session 0 feasibility | implemented; Session 0 gate pending | Interactive scan/GATT passed; must still scan before login or the platform decision reopens |
| TR-003 | SQLite profiles, immutable workouts, editor/import preview, recurring calendar and alternatives | completed | Release validation: Core 39, Protocol 34, Integration 33, Playwright 7; migration/backup round-trip |
| TR-003B | Immutable ordered 5K/10K training plans, runner-specific progression, calendar-first recommendation, and exact session provenance | completed | 285 non-browser and 38 browser checks pass; only the expected linked item ending `Completed` advances |
| TR-004 | Complete simulated live UX, leases/observers, history, sound, browser screenshots and performance | implemented | Three viewports, four-hour cadence/memory, 14,400-write SQLite soak, and loopback p95 pass; formal household-Wi-Fi latency acceptance was removed by owner decision because normal use will reveal practical connectivity issues |
| TR-017 | Local daily-use reliability, fast reuse, generated-set import, wake lock, QR access, BLE report/battery, and database integrity | implemented | Focused protocol/integration/browser tests, Release validation, security review, and populated desktop/iPhone gallery; no hardware commands |
| [TR-018](stories/tr-018-garmin-activity-upload-readiness.md) | Fault-isolated Garmin completed-activity setup and a signed self-contained offline adapter runtime | completed; 1.5.10 installed and live-ready | No credentials, MFA, Garmin upload, or treadmill commands during automated/deployment acceptance |
| [TR-005A](stories/tr-005a-ftms-capability-discovery.md) | Generic FTMS feature/range discovery with reported-versus-verified capability separation | completed | Pure read-only parsing; no outbound control |
| [TR-005](stories/tr-005-real-hardware-telemetry.md) | Omega/Polar read-only enrollment, telemetry, reconnect and soak | implemented; hardware acceptance pending | Stage 1/2 requires a named observer and approved window; no outbound control |
| [TR-005B](stories/tr-005b-anonymous-omega-enrollment.md) | Safely enroll an unnamed Omega advertisement from its observed `1816`+`1826` read-only signature | implemented | Raw advertised name remains separate; service matching never promotes controls |
| [TR-006A](stories/tr-006a-bounded-control-preflight.md) | Generic target preflight, range enforcement, non-replayable command intents and confirmation | software implemented; hardware acceptance pending | No adapter command enabled without exact-device evidence |
| [TR-006B](stories/tr-006b-capability-gated-remote-start.md) | Capability-gated hold-to-start UX and non-replayable FTMS Start/Stop | exact S3.02/V10.23.17 Start/Stop accepted | Stage 3 confirms minimum-speed Start and Stop; safety key/physical Stop remain authoritative |
| TR-006C | Full FTMS command surface, planned transitions, manual overrides, and configurable HR-speed automation | software implemented; fixed validation pending | Owner-attended sequence is frozen in the Omega Z validation runbook; no hardware command was sent while the owner was away |
| [TR-006H](stories/tr-006h-household-heart-rate-sensors.md) | Multi-sensor Polar/Garmin roster, runner assignment, auto-connect, deterministic fallback, and truthful Control status | software completed; fresh physical BPM proof pending | HR transport is read-only; private watches remain profile-bound and source changes suspend automation |
| TR-007A | Check/stage/activate signed local-folder updates, service promotion, migration, health check and rollback | software implemented; VM promotion acceptance pending | Real A→B promotion and broken C rollback on the Windows VM |
| TR-007B | CSV/FIT Activity/full backup, bounded diagnostics, previewed transactional restore, and recovery operations | software implemented; clean-install acceptance pending | Clean-install restore and recovery evidence |
| TR-007C | Durable signed stable releases installable from Operations, isolated rollback fixtures, and complete release runbook | completed; next signed package awaiting elevated feed install | UI-driven promotion to a valid newer release; no treadmill command |
| [TR-008A](stories/tr-008a-domyos-run-500.md) | Domyos Run 500 evidence and adapter | optional | Independent captures and model-specific acceptance |
| [TR-008B](stories/tr-008b-domyos-challenge-run.md) | Domyos Challenge Run evidence and adapter | optional | Independent captures; QDomyos currently has no implementation |
| TR-008 | Optional Garmin integration research and additional treadmill adapters | optional | Official API/evidence and separate fixtures |
| TR-008C | Automatic Garmin Connect delivery and Apple Health companion boundary | superseded by TR-011 for Garmin; Apple Health remains separate | Use only an officially supported Garmin integration; Apple Health requires a HealthKit-capable Apple-device companion |
| TR-009 | Close deterministic V1 gaps: capability preflight, reproducible hardware snapshots, Polar-first selection, simulated HR system flow, wake lock, BLE health, safe restore, and truthful release handoff | deterministic scope completed | No unattended BLE writes; external/elevated/long-duration proof stays explicit |
| TR-010 | Move or delete one scheduled run, shift this-and-later runs, delete a logical workout group, and improve populated calendar/workout-plan UX | completed and validated | Operation receipts, expected versions, transactional group deletion, responsive populated Playwright evidence |
| TR-011 | Per-profile Garmin account connection and automatic supported Garmin workout/calendar publishing | software implemented; live Garmin setup/acceptance pending | OAuth 2.0 only; no Garmin passwords; official Garmin program credentials and approved endpoint/payload documentation required for live sync |
| TR-013 | Per-profile unsupported completed-FIT upload plus explicit Connect IQ treadmill recording companion | software/store package implemented; external Garmin acceptance pending | Private upload disabled by default; duplicate/unknown never auto-retry; IQ SDK/simulator/exact watches/trusted HTTPS/store review remain required |
| TR-014 | Minimal iPhone live-run dashboard, meaningful chart axes, responsive populated gallery, and live-loop reliability/performance | deterministic scope implemented; final workflow validation in progress | No treadmill commands in automated validation; phone/tablet/desktop browser checks |
| [TR-016](stories/tr-016-tag-driven-github-releases.md) | Local-only tagged signed GitHub releases with no Actions builds | completed | Signing key and all build work remain local; tags are immutable and never force-moved |
| [TR-019](stories/tr-019-local-only-release-publishing.md) | Disable GitHub Actions completely and publish locally built packages directly | completed | No commit, pull request, manual dispatch, or tag can consume hosted minutes |
| [TR-020](stories/tr-020-private-lan-garmin-sign-in.md) | Allow one-time experimental Garmin login/MFA from trusted household-LAN devices and retain unattended encrypted-token uploads | implemented and installed in 1.5.12; first live import outcome requires Garmin Connect review | Public HTTP remains rejected; private-LAN HTTP is unencrypted; the first test is Unknown/no-retry and must not be resent blindly |
| [TR-021](stories/tr-021-daily-use-polish.md) | Daily-use history hygiene, Garmin acknowledgment, stale-client recovery, browser drafts, iPhone shell, and treadmill maintenance | completed; owner installed 1.5.14 through Operations | No treadmill commands, long soak, or repeated power cycles |
| [TR-022](stories/tr-022-automatic-update-reconnect.md) | Automatically recover and reload the Operations UI after an owner-confirmed update activation | implemented; owner validation on the next signed release pending | Activation remains explicit; automated tests use mocked GET/status responses and never touch the live updater |

Every story owns acceptance mapping, failing-first tests where practical, deterministic validation, documentation delta, skipped hardware checks, and finish evidence.
