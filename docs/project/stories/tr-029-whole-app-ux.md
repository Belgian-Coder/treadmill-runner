---
title: TR-029 — Whole-app responsive UX consolidation
type: user-story
status: completed
owner: project
audience: agent-and-developer
updated: 2026-08-07
---

# TR-029 — Whole-app responsive UX consolidation

## Outcome

Run now presents one primary preparation action and keeps the full workout browser, manual run, search, and recent reuse behind **Choose another run**. Pre-run checks remain expanded and actionable.

Control has three browser-session focus modes: Balanced, Chart, and Controls. The selected mode remains with the active session and resets to Balanced for the next run. Portrait and landscape retain vertical preset rails, safe-area spacing, a persistent Pause/Stop dock, and explicit 44 px touch targets.

Workouts and training plans are summary-first. Secondary revision/archive actions are in an explicit overflow, workout structure opens in a reusable full-viewport phone sheet, and the visual tokens for spacing, radii, fields, buttons, empty states, focus, and reduced motion are shared across the application.

## Safety boundary

This story changes browser presentation only. It adds no treadmill operation, reconnect command, API mutation contract, persistence change, integration, or automatic Start behavior.

## Validation

Component tests cover focus-state reset and retention. Browser tests cover the simplified Run hierarchy at desktop, tablet, and iPhone sizes and the three Control modes at desktop, tablet, iPhone portrait, and iPhone landscape sizes, including touch targets, graph axes, sticky actions, fullscreen recovery, and the single live connection inherited from TR-028. The complete responsive gallery and locally signed 1.5.19 package passed and were installed together with TR-028/TR-030.
