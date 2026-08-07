---
title: TR-026 — Workout summary and session explorer
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-06
---

# TR-026 — Workout summary and session explorer

## Outcome

As a runner choosing what to do next, I can understand the differences between saved workouts at a glance, inspect the exact segments in an existing workout, and review every ordered session in a training plan before I select or start anything.

## Acceptance boundaries

- Every ordinary workout card shows structure, expanded segments, total goal, speed range, incline range, and heart-rate use derived from its immutable current revision.
- Search includes the derived summary and structure filters distinguish steady, interval, progression, heart-rate, and multi-stage workouts.
- **View details** opens an accessible responsive dialog with the complete current revision, including nested repeats, ramps, HR targets, cues, and notes.
- Repeats remain grouped and state their expanded segment count rather than producing an unreadable flattened list.
- A detail-loading failure is isolated inside the dialog and provides a retry action.
- Every custom or premade training-plan card exposes its ordered session list; phase/week metadata remains grouped when available.
- The explorer works at desktop and iPhone 17 Pro Max portrait widths without horizontal overflow and can be closed by button, backdrop, or Escape.
- Viewing or filtering never prepares a run, mutates planning data, acquires treadmill control, or sends a treadmill command.

## Deliberate exclusions

No database migration, workout mutation, treadmill command, BLE commissioning, deployment, GitHub tag, or public release is part of this story.

## Validation

Integration coverage validates derived summaries for time/distance, HR zones, repeats, and speed/incline ranges. Playwright covers card comparison, structure filtering and search, grouped workout details, failure/retry, keyboard dismissal, custom-plan session summaries, responsive layout, and populated showcase screenshots. Sanitized evidence is stored under `docs/project/evidence/tr-026/`.
