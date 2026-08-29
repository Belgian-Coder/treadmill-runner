---
title: Local reliability, access, and generated workout sets
type: runbook
status: active
owner: project
audience: operator-and-developer
updated: 2026-08-29
---

# Local reliability, access, and generated workout sets

## Faster workout reuse

Choose a runner on **Run**. The **Run again** row shows that runner's most recently completed non-manual workouts, including the completion count and last duration. Selecting one uses the exact historical workout revision, so later edits cannot silently change it. Calendar and active-plan recommendations still take priority for the automatic suggestion.

## Keeping the dashboard awake

When a run is prepared or active, the dashboard requests the browser Screen Wake Lock. The small screen icon reports active, background-paused, released, denied, or unsupported state. The lock is requested again when the page returns to the foreground and is released after the session ends or the user leaves Control.

On iPhone, Screen Wake Lock normally requires a secure HTTPS origin. The dashboard continues to work over private HTTP, but iOS may dim the screen; use the iPhone display settings or configure a trusted local HTTPS name if needed. The app never claims a lock is active until the browser confirms it.

## Opening the app by QR code

Open **Operations → Open on another device**, put the phone on the same household Wi-Fi, and scan the QR code. If several private addresses exist, select the one used by the household network. `Gateway:PublicUrl` may define a stable local HTTP(S) address using a private IP, an unqualified household host name, or a `.local`, `.home`, `.lan`, or `.internal` name; otherwise the gateway lists private IPv4 listener addresses. Public Internet names and public IPs are rejected. QR generation is entirely local and the incoming request Host header is ignored.

## Creating and importing generated workout sets

1. Use the read-only `treadmill-workout` skill outside the application to generate a v4 multi-device bundle.
2. Do not edit the generated ZIP. Open **Workouts → Import**, select **Generated set**, and choose it.
3. Review the plan name, category, slot count, variants, and warnings.
4. Choose a policy: default primary variants, prefer HR alternatives, prefer fixed fallbacks, or prefer Omega recovery incline variants.
5. Confirm once. All selected workouts and the ordered training plan are committed in one transaction; any failure saves nothing.

The importer accepts manifest format 2, a v4 tool version, compatibility `treadmill-multi-device-bundle-v4`, and Omega profile `horizon-omega-z-dark-2023-ftms`. It verifies every listed SHA-256 hash, safe ZIP paths, size/count/expansion limits, index columns, one primary and exactly one selected variant per canonical slot, and the existing safe Omega XML parser. Uploaded bytes remain in memory for at most 15 minutes and are reparsed at confirmation.

## Bluetooth reliability and battery

Enabled devices reconnect automatically during an active session with 1, 2, 4, 8, then 10 second delays; idle demand backs off to five minutes. Manual **Connect** creates the same capped active retry cadence for two minutes. A demanded heart-rate worker performs a fresh bounded active scan before connecting and after relevant failures, asking Windows for scan-response name/service metadata so a uniquely identified Polar/Garmin source can use its current address after a long absence; ambiguous sources remain disconnected. A native disconnect, failed notification stream, GATT timeout, or 30 seconds without valid telemetry opens one outage incident; further failed attempts increment it. Valid telemetry closes the incident. A stable 30-second stream resets the backoff. Reconnect always moves to a new generation and never replays a treadmill command. Critical reliability evidence is non-dropping; disposable status churn is coalesced separately.

Open **Devices → Bluetooth reliability report** for sanitized 1, 7, 30, or 90-day results. Reports contain the enrollment label, state, outage/recovery timing, attempt count, failure category, and sanitized fault—not the raw Windows BLE identifier or identity fingerprint. Recovered incidents older than 90 days are pruned. The same seven-day summary is included in the bounded diagnostic ZIP.

For HR sensors exposing Bluetooth Battery Service `180F` / Battery Level `2A19`, Devices shows the latest validated 0–100% value and observation time. `Not reported` means the service/characteristic was absent or did not produce a valid value; heart rate continues normally.

## Database health and automatic maintenance

The gateway caches database readiness and integrity results for the configured interval; full integrity and backup work runs off the ordinary startup path and then repeats approximately daily while idle. **Operations → Database health** shows the last full check, verified backup, next check, and unresolved issues. **Check now** requests the same idle-only flow. A stale or unavailable readiness result is surfaced explicitly and never treated as a healthy database.

The flow runs bounded SQLite quick/full checks plus application semantic checks, performs a passive WAL checkpoint and `PRAGMA optimize`, removes only stale TreadmillRunner integrity-temp files, and promotes a SHA-256-verified last-known-good online backup. Three backups are retained by default (configurable from 2–10). Open **Backup and diagnostics** to download either a fresh full backup or the latest verified recovery backup. If corruption remains, readiness and Operations show recovery required; use the preview-before-restore flow. The app never deletes data or substitutes a backup automatically.

Optional service settings are `Persistence__IntegrityCheckIntervalMinutes` (15–10080), `Persistence__IntegrityBackupRetention` (2–10), `Persistence__IntegrityBackupRoot`, and `Persistence__IntegrityStatusPath`.

## Treadmill service reminders

Open **Devices → Maintenance** after enrolling the treadmill. Record the most recent inspection/service once to establish the app-distance baseline; no due warning is shown before that baseline exists. The default reminder is the earlier of three months or 241 app-recorded hardware kilometres and can be changed. A due reminder on Run is advisory and never blocks preparation or controls.

Only terminal hardware sessions recorded by TreadmillRunner count. Simulator and system-test sessions are excluded, both household profiles contribute, and console-only use is not visible. If an eligible historical hardware session is deleted, later recorded baselines are corrected transactionally so the remaining app distance stays consistent.

Before applying lubricant, verify the exact Omega Z running-surface type in its manual. Horizon says waxed surfaces must not be lubricated; the three-month/241-km guidance applies only to its silicone surfaces.

## Troubleshooting evidence

Download **Operations → Backup and diagnostics → Download diagnostics**. The schema-v2 ZIP contains current sanitized treadmill/HR state, optional HR battery, live session state, seven-day BLE incidents, and the latest database-integrity status. It contains no enrollment identifier, raw BLE identifier, Garmin credential, update key, or uploaded workout bundle, and is served with `Cache-Control: no-store`.
