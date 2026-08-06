---
title: Offline and Apple Browser Behavior
type: platform-guidance
status: reviewed
owner: project
audience: user-and-developer
updated: 2026-08-02
---

# Offline and iPhone/iPad behavior

The iPhone or iPad browser never connects to Bluetooth. It loads the Blazor WebAssembly UI from the local Windows gateway and communicates by HTTP/SignalR over Wi-Fi. The gateway continues workouts, HR automation, telemetry collection, and persistence when the browser sleeps, closes, reloads, or disconnects.

The selected v1 URL is private-LAN HTTP to avoid installing a private certificate on every Apple device. Consequently, iOS service-worker/PWA offline reload is not promised. An already loaded page can remain visible and reconnect, but a fresh reload requires the local gateway to be reachable. This is acceptable because Bluetooth and workout state also live on that gateway.

Internet access is unnecessary for workouts, history, scheduling, imports, exports, or Bluetooth. The LAN and gateway must be reachable to open or control the UI. Browser audio cues are optional and require an initial user gesture due browser autoplay rules.

Direct Web Bluetooth remains rejected: Safari/iOS support and background lifecycle cannot meet the product requirements.

## Home Screen installation and fresh-client recovery

On the household Wi-Fi, open the URL shown under Operations → Open on another device in Safari. Tap Share, choose **Add to Home Screen**, and confirm. The manifest requests standalone display and the UI handles iPhone safe areas.

TreadmillRunner deliberately does not register a service worker. The installed icon remains network-required: the NUC must be reachable and no controls or personal workout data are cached for offline use. Entry HTML, API/live responses, the manifest, and version metadata use `no-store`; fingerprinted framework/static assets retain ordinary framework caching.

The browser compares its embedded build fingerprint with `/api/system/version` at startup, when it becomes visible, and once per minute while visible. A mismatch displays **Update ready — Reload**, blocks state changes, and uses a session-scoped one-attempt-per-build reload guard. SignalR startup and reconnect attempts continue with capped jittered backoff. After recovery the browser reloads the server version, service instance, active session, devices, and lease before enabling controls; it returns to Control for an active session and Run for a terminal one. A disconnected gateway displays a persistent reconnecting state and never implies that the treadmill has stopped.
