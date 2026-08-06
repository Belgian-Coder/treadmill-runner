---
title: TR-021 Daily-Use Polish, Data Hygiene, and Maintenance
status: completed-local-package-prepared
owner: project
updated: 2026-08-06
---

# TR-021 — Daily-Use Polish, Data Hygiene, and Maintenance

TR-021 makes normal household operation easier to recover and maintain without adding any treadmill commands or cloud dependency.

## Delivered behavior

- Every session has a durable origin: Hardware, Simulator, SystemTest, or Legacy. System tests are hidden from ordinary history, totals, program progress, reusable-run suggestions, maintenance distance, and ordinary Garmin reconciliation. History has an explicit Tests view.
- Terminal, unlinked sessions can be previewed before permanent local deletion. Normal sessions with Garmin history and unfinished or ambiguous test uploads remain protected. Eligible deletions remove dependent samples/events and the eligible test job in one transaction while retaining operation receipts.
- An Unknown Garmin upload can be marked Found in Garmin after a person verifies it in Garmin Connect. This stores an acknowledgment timestamp, never invents a remote activity ID, and permanently prevents retry. Local deletion never removes the Garmin activity.
- The iPhone shell has a manifest, Apple metadata, safe-area handling, and Operations instructions for Safari's Add to Home Screen. It intentionally registers no service worker and requires the NUC to remain reachable.
- Entry documents, APIs, live state, manifests, and version metadata are no-store. The client checks the server build at startup, on visibility, and every minute. A stale build shows Update ready, blocks client mutations, and receives a server-side 409 if it attempts a state change.
- Workout, training-plan, and calendar editors keep bounded, schema-versioned browser-local drafts for 30 days. A draft is restored only after Continue draft and is cleared after save or Discard draft.
- Each enrolled treadmill receives a 3-month/241-km maintenance policy. Reminders begin only after a maintenance baseline is recorded, count terminal hardware sessions across profiles, and never block a run. Devices explains that console-only distance is invisible and that waxed surfaces must not be lubricated.

## Operational notes

The Home Screen app is a network shell, not an offline PWA. If the gateway is disconnected, controls remain unavailable and the app explicitly reminds the runner that Bluetooth disconnection is not a stop mechanism. A version reload is guarded in session storage to avoid reload loops.

Maintenance advice is informational. Confirm the exact Omega Z running-surface type in its manual before applying lubricant; the default interval follows Horizon's silicone-surface guidance only.

## Evidence

Workflow evidence is stored in `automations/user-story-workflow/runs/US-TR-021`. Locally signed version 1.5.13 is prepared under ignored release artifacts. It was not installed, tagged, published, or deployed by this story.
