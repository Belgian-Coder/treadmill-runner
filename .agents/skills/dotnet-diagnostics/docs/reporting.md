# Reporting

Use this file to turn diagnostic evidence into a bounded handoff.

## Report Shape

- Symptom: crash, hang, high CPU, memory pressure, startup failure, or unknown.
- Target: process, host, OS, architecture, runtime, container, and artifact paths.
- Evidence: stacks, exceptions, waits, counters, traces, heap summaries, roots, logs, or crash reports.
- Finding: what the evidence supports and what remains uncertain.
- Confidence: low, medium, or high with the reason.
- Next action: code fix, configuration fix, additional capture, reproduction, or monitoring change.

## Privacy

- Summarize sensitive frames, environment values, payloads, memory contents, and request data instead of copying raw values.
- Do not include tokens, connection strings, credentials, personal data, or proprietary payloads in reports.
- Keep dump and trace locations local or workflow-owned unless the user explicitly approves sharing.

## Guardrails

- Avoid root-cause claims when only collection setup succeeded.
- Report failed or skipped commands and whether existing evidence is still sufficient.
- Separate "observed evidence" from "inference".
- If no approved artifacts are available, report "insufficient evidence" with safe next captures instead of collecting live data.
- Prefer action items tied to owners: `dotnet-engineering`, `dotnet-legacy`, infrastructure, deployment, or test workflow.
