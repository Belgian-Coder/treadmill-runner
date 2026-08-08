---
title: TR-033 Resumable Stop controls and planning ownership
type: story
status: completed
owner: project
audience: agent-and-developer
updated: 2026-08-08
---

# TR-033 Resumable Stop controls and planning ownership

## Outcome

Make the two daily motion controls unambiguous and keep all workout or training-plan creation in the Plan area. A runner can safely pause and resume, explicitly end and save, or reset only the workout cursor without losing recorded totals. Calendar remains a view-and-manage surface.

## Implemented boundaries

- The large Play/Pause control sends the already hardware-verified Stop operation while running. A confirmed stop enters `PausedWaitingForPhysicalResume`; pressing and holding Play for three seconds sends a fresh, single-use Start intent. Running resumes only after fresh belt movement is observed.
- The square Stop/End control sends Stop immediately, then offers **Keep paused**, **Reset progress**, or **End and save**. End is terminal only after the treadmill is confirmed stopped.
- Reset returns the immutable workout progression cursor and workout-progress timer to the first step. It preserves elapsed recording time, distance, samples, command history, and an explicit `workout-progress-reset` event, and never starts the belt.
- Calendar no longer creates workouts, recurring schedules, or plan runs. Standalone workout scheduling and training-plan start/restart scheduling live under Plan.
- Installed premade templates are idempotent per runner and template version. The UI exposes one **Open my plan** action; no duplicate-copy action or history deletion is introduced.
- My training plans uses compact rows and a bounded detail sheet. Selecting an individual plan session continues to open the existing immutable workout-detail view.

## Acceptance status

Release acceptance passes for 1.5.23: the deterministic Release gate builds with zero warnings/errors, all 232 integration tests pass, all six Connect IQ products build, both representative Garmin simulator targets pass 4/4 tests, and the complete responsive Playwright suite passes 73/73. Software behavior covers pause, resume, Stop decisions, reset, scheduling ownership, installed-template idempotency, responsive plan details, and absence of Calendar creation controls. Exact-device safety boundaries are unchanged: the raw FTMS Pause opcode is not used by the daily UI, and all remote Start/Stop actions remain capability-, lease-, version-, and operation-ID-gated. Owner installation and physical treadmill/watch acceptance remain separate operational checks.
