---
title: TR-039 Mobile Control Layout and Landscape Graph
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-22
---

# TR-039 — Mobile control layout and landscape graph

## Outcome

Keep Balanced, Chart, and Controls available everywhere while making the two focused modes usable without scrolling on phones. Balanced remains the default for every new run. The existing HTTPS PWA remains optional and provides a standalone browser surface; this story changes responsive layout, not the PWA authority or offline boundary.

## Problem evidence

The owner supplied four iPhone screenshots on 2026-08-22. They are recorded as the visual problem baseline for this story:

| Supplied evidence | Orientation | Observed problem |
|---|---|---|
| `codex-clipboard-80c36c81-419a-4b18-ac28-f043b617b55f.png` | portrait Chart | Graph focus existed, but the browser and action layout consumed substantial vertical space. |
| `codex-clipboard-41044440-a9e3-4675-8ea7-71f57f490b3b.png` | portrait Balanced | Speed and incline preset rails required scrolling and hid controls below the fold. |
| `codex-clipboard-b8796f74-33a8-4f7a-ad3d-f9ef2faa21de.png` | landscape Balanced | The dashboard was dense and the graph expand action did not provide a landscape-focused graph. |
| `codex-clipboard-b7e057aa-b8cb-443b-9020-15fdb7fff685.png` | landscape browser/fullscreen fallback | The graph remained embedded between tall control rails instead of owning the available landscape area. |

Fresh generated evidence for the implemented modes is written by `ManualControlDashboardTests` to `output/playwright/tr-039/`. Those generated images are validation artifacts rather than maintained product screenshots.

## Acceptance contract

- Balanced, Chart, and Controls remain accessible pressed-button choices on phones, tablets, and desktop. Focus is remembered only for the active session and a new session starts in Balanced.
- Phone Controls shows measured speed, heart rate, and workout time in a compact row. Each speed and incline card shows its target, minus/plus actions, and all eight presets at once.
- Portrait Controls uses four preset columns. Landscape Controls places speed and incline cards side by side with Pause and Stop in a visible side dock.
- Phone Controls has no vertically scrolling rail, no document-width overflow, no viewport scrolling requirement, and no interactive target below 44 by 44 CSS pixels.
- Chart selection and the graph expand action use the same focused graph state. Portrait reserves the bottom action dock; landscape reserves a safe-area-aware side dock for Pause and Stop.
- Focused Chart retains Collapse, both axes, elapsed timestamps, legend, and 44 by 44 CSS-pixel actions.
- Orientation is never locked. Existing Safari/WebKit, Chromium, Firefox, iPadOS, Android, and desktop fallbacks remain responsive CSS over the existing DOM.
- Manifest, standalone display, service worker, sharing, wake lock, and offline safety behavior are unchanged. No offline workout, cached telemetry, background command, push notification, direct Bluetooth, gateway API, database, command, or protocol capability is added.

## Verification boundary

Browser coverage includes 320x800, 390x844, and 440x956 portrait; 844x390 and 956x440 landscape; 820x1180 iPad portrait; 1180x820 tablet landscape; and 1920x1080 desktop. Focused assertions cover all control bounds, internal and document overflow, fixed Pause/Stop visibility, graph fill, collapse, axes, timestamps, legend, focus persistence/reset, wake lock, reconnect behavior, fullscreen fallback, high contrast, large text, PWA installation states, and offline safety.

Real iPhone and iPad verification from a fresh HTTPS Home Screen installation remains owner acceptance after a separately authorized release and installation. Browser emulation does not prove Safari chrome removal, physical rotation, or device safe-area behavior.

The independently authorized Garmin/history work owns the intervening releases. TR-039 was not part of 1.5.53 or 1.5.54; 1.5.55 entered its signed release gate before this dashboard work. Before any separately authorized publication, reconcile with updated `main` and target 1.5.56 or later.
