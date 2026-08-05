---
name: dotnet-diagnostics
description: Use when investigating .NET crashes, hangs, deadlocks, high CPU, GC pressure, memory pressure, thread pool starvation, flame graph evidence, dumps, traces, counters, dotnet-dump, dotnet-trace, dotnet-counters, WinDbg, LLDB/SOS, createdump, dotnet-monitor, or container runtime diagnostics. Use dotnet-engineering for normal implementation and optimization work.
---

# Dotnet Diagnostics

## Goal

Collect and interpret .NET runtime evidence without guessing from source code alone, and without silently attaching to live or production processes.

## Read-Only Dogfood

Strict read-only/offline review uses routing, file inspection, `inspect-skill --fast`, and only `module.json.strict_read_only_commands`. This skill's `run_self_tests.py` is a read-only self-test exception because it only reads local skill files, sets `sys.dont_write_bytecode = True`, and does not create temp files, subprocesses, network calls, or outputs. Do not run `dotnet-*` diagnostics, attach to processes, collect counters/logs/dumps/traces, configure createdump or diagnostic ports, query symbols/endpoints, inspect sensitive production artifacts, install tools, or write evidence unless explicitly approved and bounded.

## Workflow

1. Classify the symptom: crash, hang, deadlock, high CPU, GC pressure, memory pressure, thread pool starvation, startup failure, unknown runtime failure, or performance investigation.
2. Prefer existing artifacts first: dump files, traces, counter captures, logs, test results, crash reports, container events, or CI artifacts.
3. If new evidence is required, stop until the target process, environment, data sensitivity, permissions, disk/memory headroom, and output path are explicit.
4. Choose only the needed docs:
   - `docs/runtime-triage.md` for symptom classification, flame graph, GC, thread pool, and first-pass evidence.
   - `docs/dump-and-trace.md` for dotnet-dump, dotnet-trace, dotnet-counters, WinDbg, LLDB/SOS, createdump, symbols, and containers.
   - `docs/reporting.md` for bounded findings, privacy, confidence, and handoff shape.
5. Connect findings back to owning implementation skills: use `dotnet-engineering` for modern code fixes, `dotnet-legacy` for .NET Framework fixes, and `dotnet-quality-gates` for compatible evidence parsing.

## Rules

- Do not install tools, attach debuggers, collect dumps/traces/counters/logs/snapshots, configure diagnostic collection, call dotnet-monitor endpoints, query symbol servers, or inspect production processes or production artifacts without explicit approval.
- Treat dumps, traces, environment blocks, memory, request payloads, and logs as sensitive; do not paste secrets or personal data into reports.
- Do not call a case a deadlock without wait/lock evidence.
- Do not call a memory issue a leak without retention evidence such as roots, growth over time, or repeated captures.
- Do not use diagnostic tools to publish, upload, mutate production state, or weaken security settings.
- Prefer same-OS and same-architecture analysis for dumps.
- Destructive risk is declared for runtime perturbation, live attach, diagnostic configuration, and collection side effects, not ordinary repo-file edits.

## Validation

Validate by naming the artifacts read, commands run, skipped tool prerequisites, and whether conclusions are backed by stack, heap, counter, trace, log, or dump evidence. For changes to this skill itself, run `python -B .agents/skills/dotnet-diagnostics/scripts/run_self_tests.py`. When tools are unavailable, report the exact missing prerequisite and continue only if existing evidence is sufficient.

## Stop Rules

Stop before live attach, counter/log/dump/trace/snapshot collection, diagnostic configuration changes, elevated debugging, container namespace access, production diagnostic endpoints, symbol-server network calls, tool installation, or sensitive production artifact inspection unless approval and output boundaries are clear. Stop when evidence contains sensitive data that cannot be safely summarized.

## Completion Contract

Report symptom classification, target process or artifact paths, selected docs, commands run, generated artifacts, validation result, optional setup or tooling skipped/failed, whether it is non-blocking, whether work can continue, skipped or blocked checks, failed command summaries, confidence level, and remaining diagnostic risk.
