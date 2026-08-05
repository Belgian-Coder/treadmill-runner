---
title: TreadmillRunner Agent Rules
type: repository-policy
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# TreadmillRunner agent rules

- Reviewed PowerShell entry points are allowed only under `eng/*.ps1`; repeated Windows service, packaging, hardware-diagnostic, and validation behavior belongs there instead of ad hoc recipes.
- Remote treadmill belt Start is currently disabled. It requires its own approved story, exact model/firmware evidence, and the dedicated non-replayable Start gate defined by project safety policy; ordinary protocol evidence or another command's approval cannot authorize it. All real BLE writes require stage-specific owner approval and sanitized evidence defined by the project safety policy and protocol-evidence skill.
- Use only `user-story-workflow` for product stories. Start it with the story identity; the harness stores it as `US-<identifier>` (for example `US-TR-001`). Bug runs use `BUG-<identifier>`. Never use dates as ticket run-folder names; dates belong in `run.json`, `REPORT.md`, and `execution-log.md`.
- Keep retained run evidence lean: `plan.md`, `REPORT.md`, and `run.json`; keep `execution-log.md` only while a story remains active. Do not retain generated context/checkpoint mirrors after closeout.
- Use TDD for domain/protocol behavior and run the scoped verifier plus `eng/validate.ps1` before story finish.
- WinRT types may appear only in `TreadmillRunner.Infrastructure`; Core, Protocols, and Web remain portable.
- Only the serialized device coordinator may issue GATT writes. Reconnect invalidates queued commands and may return only to `Ready`.
- Update root `project-context.md` and an ADR when architecture, runtime, safety behavior, persistence, or deployment commands change.
- Browser evidence uses 390x844 phone portrait, 1180x820 tablet landscape, and 1920x1080 desktop viewports.
- Resolve `.agents/orchestration.json` before model selection or delegation. Task routes define priorities and fallbacks, while the primary orchestrator decides dynamically whether a subagent is worth its coordination cost.
