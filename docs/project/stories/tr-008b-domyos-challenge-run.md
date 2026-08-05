---
title: TR-008B Domyos Challenge Run Adapter
type: user-story
status: optional
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# TR-008B — Domyos Challenge Run evidence and adapter

As a future owner of a Domyos Challenge Run, I want its protocol investigated without coupling the application to Horizon behavior so that support can be added when real evidence exists.

## Acceptance

- Treat the model as unknown until owner-controlled advertisement, GATT, telemetry, and companion-app captures establish its protocol.
- Add a new `ITreadmillProtocol` adapter or reuse a generic FTMS adapter only when fingerprints and fixtures prove the choice.
- Keep model detection deterministic and do not use broad `Domyos` name matching that could select the wrong protocol.
- Enable ranges and commands independently per model/firmware; Start follows TR-006B.
- Record unsupported fields and behavior rather than borrowing Horizon or Run 500 assumptions.

QDomyos issue [#4816](https://github.com/cagnulein/qdomyos-zwift/issues/4816) requested Challenge Run support and was closed `wontfix` without an implementation, so it is a research lead rather than evidence of compatibility.
