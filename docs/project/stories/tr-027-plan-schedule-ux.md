---
title: TR-027 — Plan schedule UX and global runner context
type: user-story
status: completed
owner: project
audience: agent-and-developer
updated: 2026-08-07
---

# TR-027 — Plan schedule UX and global runner context

## Outcome

As a runner, I choose myself once in the application header, see premade sessions inside their training plan instead of as dozens of unrelated workouts, and can safely adapt the plan calendar without losing its order.

## Calendar behavior

The complete logical plan is derived from its start date and selected training days. The database stores only sparse exceptions:

- **Move only** changes one unfinished session and leaves later dates unchanged.
- **Move this and following** shifts the selected unfinished session and every later unfinished plan session by the same offset.
- **Skip** advances progression without claiming the session was completed.
- **Restore** removes the selected move or skip and returns that item to its derived date.
- **Repeat · keep dates** inserts an extra attempt for an already completed session without moving the remaining plan.
- **Repeat · shift the rest** inserts that extra attempt and moves later incomplete plan sessions forward.

Every change is previewed before confirmation. A date may contain more than one session when the calendar is already full; the preview names those collisions and nothing is overwritten. A stopped, interrupted, or faulted attempt remains incomplete and is rescheduled rather than repeated. A completed but unsatisfactory attempt remains completed for progression and can receive a separate repeat occurrence.

Extra repeats never rewind progression and never fabricate another completion. They are presented as an additional calendar attempt. The runner may still choose either scheduled workout on a collision date.

## Data and safety boundaries

Schedule changes are profile-, run-, version-, and operation-scoped. Replays return the original result, concurrent changes are rejected, and completed base sessions cannot be moved, skipped, or restored. Plan-internal revisions are identified from durable template-program provenance, including legacy rows whose stored kind predates `PlanInternal`; custom reusable workouts remain in the normal library.

No action prepares a workout, acquires treadmill control, starts the belt, or sends a Bluetooth command.

## Validation

Core tests cover full projection, sparse overrides, and skipped progression. Integration coverage checks ownership, version conflicts, collisions, repeats on a full calendar, and 174-session shifting. Playwright covers the global runner picker and the preview/confirm calendar action sheet at iPhone and desktop widths. Sanitized evidence is stored under `docs/project/evidence/tr-027/`.

Locally signed release 1.5.18 was staged from its signed offline bundle and activated through the installed updater. The restarted Windows service reported 1.5.18, a new build fingerprint, HTTP 200 readiness, no active session, and retained profile, workout, and plan data. No GitHub tag or public release was created.
