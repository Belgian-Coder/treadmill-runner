---
title: TR-031 Local-first household improvements
type: story
status: software-complete
owner: project
audience: agent-and-developer
updated: 2026-08-07
---

# TR-031 Local-first household improvements

## Outcome

Deliver the ten-part household roadmap without adding external services: retain hardware acceptance gates; complete the Garmin metrics page; add profile-owned readable displays; separate recording, manual-control, and planned-automation readiness; explain and compare completed runs; persist deterministic progression advice and decisions; add local trends/goals; configure local cues; rotate and verify owner-selected local/UNC backups; and suggest a runner from one fresh assigned HR sensor only after confirmation.

## Implemented boundaries

- Garmin Ready/timer optical alignment remains shared, while a next/previous-page metrics view shows native heart rate, distance, speed, and calories with `--` for unavailable values.
- Live display preferences select two or three distinct metrics and balanced, large-text, or high-contrast mode. Stop, safety, connection, and automation-suspension state are never hidden.
- Readiness checks are read-only and never start or prepare motion. Manual and planned control are reported separately.
- Comparisons use only the exact immutable workout revision. Trends exclude simulator and system-test sessions and label incomplete telemetry.
- The `local-progression-v1` adviser derives its result from stored completion, adherence, RPE, HR coverage, interruptions, and telemetry. Acceptance/rejection is versioned; neither action rewrites a plan or workout.
- Browser cues use profile-owned type/volume preferences and never imply treadmill command success.
- Backup policy paths must be absolute local or UNC paths. Scheduled/manual copies acquire the idle maintenance lease, run an isolated full SQLite integrity check, retain 2–60 owned backup files, and persist success/failure receipts.
- Quick start considers assigned, selected, fresh HR sensors. One unambiguous match is suggested with confirmation; active-session runner identity cannot switch.
- Workout-library cards, calendar occurrences, and history cards open responsive detail sheets. Planned items show the immutable expanded plan graph and every change in execution order; completed sessions show persisted planned/requested/measured telemetry and a truthful ordered event table. The sheets do not expose treadmill motion controls.

## Acceptance status

Software acceptance is complete. The deterministic Release gate passes with zero warnings/errors; Core 148/148, Protocols 93/93, and Integration 227/227 pass. Connect IQ SDK 9.2.0 builds all six products and passes 4/4 tests on both representative simulator targets. The populated Web gallery, local-first internet-blocked flow, and large-text/high-contrast Control layouts pass at 390x844, phone landscape, 1180x820, and 1920x1080 with rendered evidence under `screenshots/showcase/tr-031-*`. Focused browser acceptance also proves workout-library, calendar, and history detail sheets on desktop and phone, including graph/change-table contents, responsive overflow, and absence of treadmill Start/Stop controls. Backup rotation, isolated full-integrity verification, disk-full warning behavior, restart reconciliation, and BLE interruption policies have deterministic coverage.

Computer Use can enumerate the Garmin Simulator window, but `sky.get_window_state()` remains blocked by the plugin runtime (`node_repl exec context not found`), so no new automated Garmin metrics-page UI claim is added beyond the existing representative simulator evidence. Physical Omega/Polar, power-cycle, Session 0, representative-watch trusted pairing/export, and installed signed-release acceptance still require the owner and exact devices; Pause remains disabled until that acceptance succeeds.
