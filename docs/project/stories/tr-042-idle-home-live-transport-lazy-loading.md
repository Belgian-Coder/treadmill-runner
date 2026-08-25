---
title: TR-042 Idle Home live-transport lazy loading
type: story
status: implemented-and-release-approved
owner: project
audience: agent-and-developer
updated: 2026-08-25
---

# TR-042 — Idle Home live-transport lazy loading

## Outcome

Keep the SignalR client and its transport dependencies out of the idle Home download while preserving active-session recovery, direct Control navigation, and explicit Prepare-run authority acquisition.

## Implemented boundaries

- The existing nine-assembly live transport group is loaded by `GatewayConnectionSupervisor` immediately before it creates the SignalR client. Route loading remains responsible only for the Operations assembly.
- Idle Home performs one read-only `GET api/live/session`. An active, armed, running, or paused session redirects to Control, which then loads the live transport. The probe never acquires a lease or sends a treadmill command.
- The first explicit control request starts the on-demand connection and waits for the existing connection task for at most 15 seconds before evaluating lease readiness. Commands are neither retried nor replayed.
- Direct `/control` navigation remains supported and loads the same live group through the supervisor.

## Acceptance and evidence

The failing-first browser assertion originally observed `TreadmillRunner.Web.SignalR` on idle Home. After implementation, the focused browser test passed with zero live-group requests on Home and a SignalR request after navigation to Control; focused gateway connection tests also passed.

The prior Home Lighthouse reports transferred 122,278 bytes for the nine live resources (118,768 Brotli body bytes plus response overhead). The new Home reports request none of those resources. Clean-Chrome Lighthouse performance improved from 60 to 63 on mobile and from 64 to 68 on desktop. Mobile FCP/LCP/TBT/TTI changed from 1.182/1.745/10.592/25.065 seconds to 1.134/1.655/4.600/19.091 seconds; desktop changed from 0.317/0.421/1.608/4.949 seconds to 0.317/0.416/0.837/3.758 seconds. Accessibility, Best Practices, and SEO remained 100. Valid reports are under `artifacts/performance/lighthouse-20260825-tr042`; Lighthouse reported its known Windows temporary-profile cleanup error only after writing them.

## Safety and scope

This story adds no server endpoint, persistence change, command path, automatic Start, command retry, DNS behavior, Caddy configuration, or physical-equipment acceptance. The gateway remains authoritative for sessions, leases, Bluetooth, and commands.

## References

- [Live-session authority and recovery](../live-session.md)
- [TR-042 workflow run](../../../automations/user-story-workflow/runs/US-TR-042/REPORT.md)
