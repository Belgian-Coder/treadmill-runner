---
title: Offline and Apple Browser Behavior
type: platform-guidance
status: reviewed
owner: project
audience: user-and-developer
updated: 2026-08-11
---

# Offline and iPhone/iPad behavior

The iPhone or iPad browser never connects to Bluetooth. It loads the Blazor WebAssembly UI from the local Windows gateway and communicates by HTTP/SignalR over Wi-Fi. The gateway continues workouts, HR automation, telemetry collection, and persistence when the browser sleeps, closes, reloads, or disconnects.

The preferred household URL is now a trusted private-LAN HTTPS origin. HTTPS enables standards-gated browser installation, standalone display, native file sharing where supported, Screen Wake Lock, and one narrowly scoped offline safety document. It does not move Bluetooth, session timing, persistence, or command authority into the browser.

Internet access is unnecessary for workouts, history, scheduling, imports, exports, or Bluetooth. The LAN and gateway must be reachable to open or control the UI. Browser audio cues are optional and require an initial user gesture due browser autoplay rules.

Direct Web Bluetooth remains rejected: Safari/iOS support and background lifecycle cannot meet the product requirements.

## Home Screen installation and fresh-client recovery

On the household Wi-Fi, open the HTTPS URL shown under Operations → Open on another device in Safari. Tap Share, choose **Add to Home Screen**, and confirm. Chrome or Edge can use **Install app** or the browser-menu install command. The manifest requests standalone display, publishes Run/Calendar/History/Operations shortcuts, and asks supporting Chromium browsers to navigate an existing installed window. Unsupported launch-handler and shortcut behavior degrades to normal launching.

On HTTPS or loopback, the early browser bridge registers a root-scoped service worker. Its private versioned cache contains exactly `/offline.html`. Only top-level navigation is intercepted, using the current network response first; network failures and gateway-proxy 502/503/504 responses show the safety document. The worker does not force activation or claim an already open client.

The worker never handles or caches API, SignalR, Blazor/framework assets, app shell files, workouts, history, drafts, telemetry, exports, credentials, or commands. The safety document is self-contained, shows no remembered state, says that treadmill/gateway state is unknown, and warns that Wi-Fi or Bluetooth loss does not stop the belt. The physical console, safety key, and physical Stop control remain authoritative. Restoring connectivity and navigating again always loads the current server application rather than a cached UI.

An HTTP installation and HTTPS installation have different browser identities. Reinstall from HTTPS rather than expecting an old HTTP Home Screen icon to upgrade in place. `beforeinstallprompt` and file sharing are progressive browser capabilities; Operations gives Safari/browser-menu instructions when the prompt is unavailable, and ordinary downloads remain visible when native sharing is unsupported.

The browser compares its embedded build fingerprint with `/api/system/version` at startup, when it becomes visible, and once per minute while visible. A mismatch displays **Update ready — Reload**, blocks state changes, and uses a session-scoped one-attempt-per-build reload guard. SignalR startup and reconnect attempts continue with capped jittered backoff. After recovery the browser reloads the server version, service instance, active session, devices, and lease before enabling controls; it returns to Control for an active session and Run for a terminal one. A disconnected gateway displays a persistent reconnecting state and never implies that the treadmill has stopped.
