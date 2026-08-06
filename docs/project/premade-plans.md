---
title: Premade training plans
type: operator-and-developer-guide
status: active
owner: project
audience: user-and-developer
updated: 2026-08-06
---

# Premade training plans

Open **Workouts → Premade plans** after selecting a runner on Run. Search or filter the catalog, select a plan, and review its length, sessions, phases, heart-rate requirements, normalized maximum speed/incline, and compatibility result. **Add to my training** creates an inactive, profile-owned copy. It does not prepare a session, activate a plan, start a belt, or send a treadmill command.

The same template version is added only once per runner by default. **Already added** links the catalog state to that durable receipt. Use **Add fresh copy** when a second independent progression is intentional. Existing copies do not change when a catalog template is updated.

Installed premade plans are immutable. The Training plans view groups long plans by phase and week, and expands only the week the runner wants to inspect. Starting or restarting one remains a separate explicit action with the normal confirmation flow.

## Included catalog

The catalog covers beginner and performance 5K/10K plans, heart-rate variants, general fitness, walking/recovery, maintenance cycles, and the 58-week 5K-to-10K distance-first progression. Heart-rate sessions store Z1–Z5 references, never copied personal BPM values; BPM is resolved from the selected runner during session preparation.

## Source and safety provenance

The catalog was independently authored from read-only owner-provided examples, configuration patterns, and a long-plan bundle. Personal weight, BPM values, sensor IDs, gait preferences, machine paths, and account data were not copied. The plans are not official exports from Horizon, Garmin, QDomyos, or another provider and do not make medical or rehabilitation claims.

Every generated target is evaluated against the runner maximum and the verified Omega Z ranges when enrolled. Normalization never makes a request more aggressive. Missing required HR zones or a rejected target blocks materialization with an explanatory preview message.

Generic standalone QDomyos XML remains conservative. A generated v4 bundle is promoted to a bounded HR directive only when it explicitly supplies a positive HR target together with initial, minimum, and maximum speed bounds.
