---
title: TR-022 Automatic Update Reconnect
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-06
---

# TR-022 — Automatic update reconnect

As the household operator, I want the Operations page to recover automatically after an accepted update activation so that a successful service restart does not remain visibly stuck on `Activating` until I refresh the browser.

## Acceptance boundaries

- Activation remains a separate explicit owner confirmation. This story does not check, stage, or activate an update automatically.
- The browser starts recovery after an accepted activation response or an interrupted/unknown response. The unknown path is GET-only and never resends activation. Normal stale clients retain the existing **Update ready — Reload** choice.
- Recovery uses one-second read-only version/status checks with a hard three-minute cancellation deadline and is cancelled when the component is disposed.
- A new build fingerprint uses a one-attempt automatic reload guard, preserves the current Operations URL with a `build` query marker, and returns to the Signed updates card.
- Automatic recovery still works when browser session storage is unavailable. The explicit banner **Reload** action bypasses a spent automatic guard and adds a fresh cache-busting marker.
- A rollback returning on the same build renders its terminal status without reloading.
- Disconnect remains truthful and never implies that Bluetooth stopped the treadmill.
- Automated acceptance uses mocked browser routes only; it does not activate the installed service or issue a treadmill command.

## Acceptance evidence

- A failing-first Playwright regression reproduced the original behavior: the page stayed on `/operations` and `Activating` until timeout.
- The promoted-build regression proves an interrupted activation response, a refused version connection, disabled session storage, and then a new fingerprint still cause exactly one reload.
- The rollback regression proves the same build updates to `RolledBack` with no document reload.
- The phone-sized manual-reload regression proves the explicit action bypasses a spent automatic guard, returns to Signed updates, and introduces no horizontal overflow.
- Final owner acceptance requires installing a newer signed release through Operations and observing automatic recovery without manually refreshing.
