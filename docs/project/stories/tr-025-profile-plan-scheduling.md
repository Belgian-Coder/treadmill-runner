---
title: TR-025 — Profile-owned plan scheduling and ordered progression
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-06
---

# TR-025 — Profile-owned plan scheduling and ordered progression

## Outcome

As either household runner, I can add a premade plan to my own profile, choose its calendar start and training days, and always see the next sessions in their intended order without exposing the plan or its generated workouts to the other runner.

## Acceptance boundaries

- Workouts shows an explicit runner context and reloads catalog receipts and installed plans when that runner changes.
- Materialized plans are visible, startable, and progressable only for their owner profile; a cross-profile start is rejected by the API.
- Generated plan workouts are internal and excluded from the shared workout library and manual/calendar selectors.
- Adding a plan immediately offers scheduling, while **Keep for later** preserves an inactive copy.
- Starting a premade plan requires a first date and the template's exact number of weekly training days.
- The active run stores its schedule and deterministically projects ordered program items onto that runner's calendar.
- Calendar entries expose plan, position, total, phase, and week and retain the exact program run/item identity when selected on Run.
- Repeated workout definitions remain separate ordered program positions; completing only the selected position advances progression.
- Existing pre-release test copies do not receive a compatibility conversion; the owner confirmed that only disposable test data exists.
- No scheduling or navigation action prepares a session, sends a Bluetooth command, or starts the treadmill.

## Deliberate exclusions

No backward-compatibility layer for pre-release clients, deployment, GitHub tag, public release, treadmill movement, BLE commissioning, long soak, or power-cycle test is part of this story.

## Validation

Core projection tests cover exact order and range stability. Integration tests cover profile isolation, internal-workout hiding, durable schedules, calendar projection, and long-plan positions. Playwright covers runner context, add/schedule/start, ordered calendar visibility, grouped long plans, and phone layout. Sanitized evidence is stored under `docs/project/evidence/tr-025/`.
