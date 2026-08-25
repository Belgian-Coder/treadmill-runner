---
title: Session and workout exports
type: reference
status: current
owner: project
audience: runner-operator-and-developer
updated: 2026-08-23
---

# Session and workout exports

Exports are generated from immutable local source data. They are read-only downloads and never change a session, sample, event, debrief, or workout revision. Values use Metric units.

| Route | Format | Source and contract |
|---|---|---|
| `GET /api/history/{sessionId}/export.tcx` | TCX Activity | Completed session trackpoints with elapsed time, distance, speed, incline-derived elevation, and available heart rate. |
| `GET /api/history/{sessionId}/export.json` | Versioned native JSON | Full-resolution session snapshot, samples, events, debrief, source provenance, and exporter version. |
| `GET /api/planning/workouts/revisions/{revisionId}/export.fit` | FIT Workout | One immutable workout revision with standards-based workout/step messages; unsupported directives fail explicitly rather than being silently dropped. |

Existing CSV and FIT Activity exports remain available for completed sessions. Display downsampling never changes export fidelity: History charts may show a bounded representative projection while export routes read the authoritative stored samples.

Garmin completed-activity upload may consume the FIT Activity export only after its profile is explicitly enabled and the bounded matching/lease rules allow it. Official Training API publishing remains setup-only until Garmin approval, a reviewed contract adapter, and credentials exist.

See [Planning data and import flow](planning-data.md), [Garmin integrations](garmin-connect.md), and [WalkingPad plan provenance](walkingpad-plan-provenance.md) for persistence, reconciliation, and source-hash boundaries.
