---
title: Premade training plans
type: operator-and-developer-guide
status: active
owner: project
audience: user-and-developer
updated: 2026-08-06
---

# Premade training plans

Open **Workouts → Premade plans**, choose the runner at the top of the page, search or filter the catalog, then review a plan's length, sessions, phases, heart-rate requirements, normalized maximum speed/incline, and compatibility result. **Add for _runner_** creates an inactive copy owned only by that profile. The generated plan workouts are internal building blocks: they do not clutter the shared workout library or manual-workout selector.

The same template version is added only once per runner by default. **Already added** links the catalog state to that durable receipt. Use **Add fresh copy** when a second independent progression is intentional. Existing copies do not change when a catalog template is updated.

After adding, choose the first calendar date and the required training days. **Start plan** activates the ordered progression and projects every session onto that runner's calendar. **Keep for later** leaves the copy inactive. Calendar entries show the plan name, exact position (for example workout 7 of 174), phase, and week; selecting an entry returns to Run with the exact program item rather than a generic workout copy. Only completion of that exact program item advances the plan.

Installed premade plans are immutable and remain profile-scoped. The Training plans view groups long plans by phase and week, and expands only the week the runner wants to inspect. Starting or restarting one remains a separate explicit action with the normal confirmation flow. A second runner adds and schedules their own independent copy, progress, and calendar.

## Included catalog

The catalog covers beginner and performance 5K/10K plans, heart-rate variants, general fitness, walking/recovery, maintenance cycles, and the 58-week 5K-to-10K distance-first progression. Heart-rate sessions store Z1–Z5 references, never copied personal BPM values; BPM is resolved from the selected runner during session preparation.

## Source and safety provenance

The catalog was independently authored from read-only owner-provided examples, configuration patterns, and a long-plan bundle. Personal weight, BPM values, sensor IDs, gait preferences, machine paths, and account data were not copied. The plans are not official exports from Horizon, Garmin, QDomyos, or another provider and do not make medical or rehabilitation claims.

Every generated target is evaluated against the runner maximum and the verified Omega Z ranges when enrolled. Normalization never makes a request more aggressive. Missing required HR zones or a rejected target blocks materialization with an explanatory preview message.

Generic standalone QDomyos XML remains conservative. A generated v4 bundle is promoted to a bounded HR directive only when it explicitly supplies a positive HR target together with initial, minimum, and maximum speed bounds.
