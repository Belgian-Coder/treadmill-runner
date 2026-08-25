---
title: TR-040 Interactive Chart Inspector
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-22
---

# TR-040 — Interactive chart inspector

## Outcome

Make active and recorded workout graphs inspectable without changing their telemetry or rendering authority. Hover, touch, or keyboard selection chooses the nearest rendered timestamp, draws a crosshair distinct from the live progress cursor, and shows an in-chart popover for every visible series.

## Interaction contract

- Mouse hover follows the nearest plotted timestamp. Leaving the graph dismisses an unpinned inspection.
- Touch or pen input pins the selection and supports horizontal dragging. Tapping outside the graph dismisses it.
- Keyboard focus starts at the newest point. Left, Right, Home, and End move through plotted timestamps; Escape dismisses the inspection.
- Speed and incline graphs show Plan, Target, and Measured values with their units. Historical heart-rate graphs show the measured BPM value.
- Missing samples display an em dash. The client does not interpolate or query another dataset.
- The tooltip flips and clamps horizontally inside the graph, including the TR-039 portrait and landscape Chart layouts. Pause and Stop remain outside and visible.
- Accessible keyboard changes are announced through a polite status region. The SVG retains its descriptive image label.

## Data and safety boundary

Inspection points are derived from the same bounded, rendered timestamps used to build the visible paths. Live selection remains transient browser state across chart refreshes and is never persisted. This story adds no gateway API, database, command, Bluetooth, Garmin/FIT, PWA cache, background, or offline-control behavior.

US-TR-041 owns the new FIT/TCX/native export and Garmin reconciliation evidence contracts. TR-040 owns chart UI, its focused browser tests, and these documents only. With 1.5.54 published and 1.5.55 in the signed release gate, this unreleased dashboard work targets 1.5.56 or later and still requires separate release authorization.

## Verification boundary

Focused browser acceptance covers hover values, touch pin/dismiss behavior, keyboard navigation and announcement, unavailable values, current-versus-inspection cursor separation, tooltip edge containment, historical speed/incline and heart-rate inspection, responsive overflow, and continued Pause/Stop visibility.

Playwright confirms browser behavior, not physical Safari behavior. Final iPhone and iPad acceptance remains an owner-attended check after a separately authorized HTTPS Home Screen release and installation.
