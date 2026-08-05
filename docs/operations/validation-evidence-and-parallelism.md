---
title: Validation Evidence And Parallelism
type: guide
status: active
owner: workflow-manager
audience: agent
updated: 2026-05-27
---

# Validation Evidence And Parallelism

Default workflow execution is sequential. Parallelism belongs in deterministic Python scripts with explicit inputs/outputs.

## Evidence Packet

Common fields: `schema_version`, `tool`, `ok`, `status`, `summary`, `checks`, `skipped`, `commands`, `evidence_paths`.

Each `checks[]` item names check, result, summary, command/artifact, and evidence path. Cap successful output; keep larger logs for failures. Write failed evidence when a child command, parser, or endpoint fails.

## Parallel Boundaries

Scripts may run independent checks concurrently when inputs are stable and outputs do not share write targets. Side agents are optional read-only reviewers for failed, ambiguous, large, or security-sensitive evidence.

Side agents must not be required for correctness, run analyzers instead of scripts, write files, share write targets, or make integration decisions. Report unsupported side-agent execution as skipped and continue sequentially.

## Workflow Extensions

Bind project behavior in workflow `module.json`: related module, input, wrapper command, output path, required flag, and fallback. Wrappers live under `automations/<workflow>/scripts/`, use Python 3.12+ stdlib, and write evidence under the run folder.
