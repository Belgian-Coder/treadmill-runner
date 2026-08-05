---
title: TR-008A Domyos Run 500 Adapter
type: user-story
status: optional
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# TR-008A — Domyos Run 500 evidence and adapter

As a future owner of a Domyos Run 500, I want it supported through a separate protocol adapter so that the runner engine, workout model, UI, and persistence remain unchanged.

## Acceptance

- Capture the exact advertised identity, services, characteristics, feature/range values, notification packets, and firmware/model identity from owner-controlled hardware.
- Reuse generic FTMS parsing when the machine actually exposes it; keep Domyos-specific framing, connection liveness, and command quirks inside the adapter.
- Do not infer support from QDomyos model labels. Existing issue reports show Run 500-specific connection/liveness problems and require our own fixtures: [QDomyos issue search](https://github.com/cagnulein/qdomyos-zwift/issues?q=is%3Aissue+%22RUN500%22).
- Enable each command, including Start, independently after the corresponding hardware gate.
- Run the same read-only, reconnect, soak, bounds, command confirmation, and no-replay acceptance used for Omega Z.
