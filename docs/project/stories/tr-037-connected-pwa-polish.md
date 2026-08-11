---
title: TR-037 Connected PWA Polish and Offline Safety
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-11
---

# TR-037 — Connected PWA polish and offline safety

## Outcome

Use the trusted private-LAN HTTPS origin to improve installation and outbound sharing while keeping the NUC authoritative. The browser remains a replaceable connected view, not an offline treadmill controller.

## Acceptance contract

- The manifest keeps root identity, standalone display, and root scope; it adds Run, Calendar, History, and Operations shortcuts plus progressive `navigate-existing` launch handling.
- Operations reports installed, browser-prompt, or manual Safari/browser-menu installation guidance and explains that an HTTP installation must be replaced from HTTPS.
- The early PWA bridge registers the root worker only in a secure context (HTTPS or browser-trusted loopback), captures Chromium's optional install prompt, and exposes explicit-click share-or-download behavior.
- The versioned private worker cache contains exactly `/offline.html`. Only top-level GET navigation uses network-first fallback on transport failure or 502/503/504. The worker never forces activation or claims open clients.
- The offline document contains no personal/live state and warns that gateway and treadmill state are unknown, Wi-Fi/Bluetooth loss does not stop the belt, and the physical console, safety key, or physical Stop control must be used.
- API, SignalR, Blazor/framework assets, app shell, workouts, history, drafts, telemetry, exports, credentials, and commands remain network-only.
- Session CSV/FIT and full/verified backups retain visible Download actions. Share fetches only after a click, preserves server filename/type, checks `navigator.canShare({ files })`, ignores user cancellation, downloads unsupported file types, and reports other failures truthfully. Diagnostics remain download-only.
- There is no database migration, API addition, notification/push subscription, background task/sync, badge, direct Web Bluetooth, protocol handler, inbound file/share handler, or treadmill command change.

## Verification boundary

Integration coverage verifies manifest identity/shortcuts/launch handling, content types, no-store headers, and worker/offline safety source contracts. Browser coverage exercises phone/tablet/desktop install states, successful/unsupported/cancelled/failed file sharing, one-entry cache contents, offline navigation safety text, network-only request failure, and live recovery after connectivity returns. Stale-client, reconnect, active-session, and wake-lock coverage remain part of the full project gate. Connect IQ is excluded because no companion source changed.

Owner acceptance occurs separately on the configured HTTPS origin and issues no treadmill command.
