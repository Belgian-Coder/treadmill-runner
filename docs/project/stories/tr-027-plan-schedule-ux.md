---
title: TR-027 — Plan schedule UX and global runner context
type: user-story
status: completed
owner: project
audience: agent-and-developer
updated: 2026-08-29
---

# TR-027 — Plan schedule UX and global runner context

## Outcome

As a runner, I choose myself once in the application header, see premade sessions inside their training plan instead of as dozens of unrelated workouts, and can safely adapt the plan calendar without losing its order.

## Calendar behavior

The complete logical plan is derived from its start date and selected training days. The database stores only sparse exceptions:

- **Move only** changes one session and leaves later dates unchanged. For a completed base session, it corrects only the calendar date and preserves the completed run and progression.
- **Move this and following** also supports a completed-late selected session: moving it to the actual completion date shifts every later incomplete session by the same offset while preserving History and progression.
- **Move this and following** shifts the selected unfinished session and every later unfinished plan session by the same offset.
- **Skip** advances progression without claiming the session was completed.
- **Restore** removes the selected move or skip and returns that item to its derived date.
- **Repeat · keep dates** inserts an extra attempt for an already completed session without moving the remaining plan.
- **Repeat · shift the rest** inserts that extra attempt and moves later incomplete plan sessions forward.

Every change is previewed before confirmation. Ordinary moves and schedule shifts are blocked when their target date is occupied. An explicit repeat may share an already-full date only after the preview names the collision; nothing is overwritten. A stopped, interrupted, or faulted attempt remains incomplete and is rescheduled rather than repeated. A completed but unsatisfactory attempt remains completed for progression and can receive a separate repeat occurrence.

Extra repeats never rewind progression and never fabricate another completion. They are presented as an additional calendar attempt. The runner may still choose either scheduled workout on a collision date.

## Data and safety boundaries

Schedule changes are profile-, run-, version-, and operation-scoped. Replays return the original result and concurrent changes are rejected. A completed base session may move by itself to correct its calendar date, or move with every later incomplete session by one shared offset when it was completed late. It cannot be skipped or restored, a later completed item blocks a shift across it, and neither move rewrites the linked completed History record. Plan-internal revisions are identified from durable template-program provenance, including legacy rows whose stored kind predates `PlanInternal`; custom reusable workouts remain in the normal library.

No action prepares a workout, acquires treadmill control, starts the belt, or sends a Bluetooth command.

## Validation

Core tests cover full projection, sparse overrides, and skipped progression. Integration coverage checks ownership, version conflicts, collisions, repeats on a full calendar, and 174-session shifting. Playwright covers the global runner picker and the preview/confirm calendar action sheet at iPhone and desktop widths. Sanitized evidence is stored under `docs/project/evidence/tr-027/`.

Locally signed release 1.5.18 was staged from its signed offline bundle and activated through the installed updater. The restarted Windows service reported 1.5.18, a new build fingerprint, HTTP 200 readiness, no active session, and retained profile, workout, and plan data. No GitHub tag or public release was created. That historical installation predates the completed-session date correction described above and is not release or installation evidence for the current source change.
