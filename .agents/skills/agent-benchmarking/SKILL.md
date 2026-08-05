---
name: agent-benchmarking
description: Use when preparing, recording, estimating, or comparing agent benchmark runs and model/tool/workflow token, cached-input, cost, quality, latency, skipped-check, or recurring-mistake evidence.
---

# Agent Benchmarking

## Goal

Produce comparable, receipt-backed model and workflow evidence without confusing estimates, subscription credits, API-equivalent prices, or evaluator output.

## Preferred Config

For a difficult decision-complete cross-layer story, keep one `gpt-5.6-terra` agent at `high` through implementation, then run the complete deterministic gate. Stop when green. When the gate yields a bounded failure packet, give a fresh `gpt-5.6-luna` agent at `high` the story, changed-file summary, exact failures, and mandatory final commands; rerun the complete gate. US-TR-008 reached 100/100 eligibility at 62.292776 credits/$2.491711 API-equivalent, versus 111.405480 credits for an equally successful Sol-medium repair. Avoid a separate planner or open-ended reviewer for decision-complete work. Escalate to Sol-medium/high for safety, protocol, security, recovery, architecture, unresolved ambiguity, or a repeated Luna failure. This is provisional until three matched repetitions pass. See `docs/real-story-routing-benchmark.md`.

Print the machine-readable policy:

```shell
python -B .agents/skills/agent-benchmarking/scripts/codex_exec_measure.py preferred-config
```

## Workflow

1. Freeze the story, starting commit, fixtures, validation commands, quality rubric, CLI/model/reasoning, pricing snapshot, and isolation roots. Use identical inputs for every arm.
2. Run the meter `self-test`. Keep threads persistent, ignore user config for model comparisons, use read-only by default, and permit writes only inside an authorized disposable benchmark root.
3. Measure each exact UTF-8 prompt with `codex_exec_measure.py run`; it records prompt hash, wall time, thread ID, provider usage fields, credits, and API-equivalent price. `output_tokens` already contains reasoning tokens. Recover a stranded rollout only with `recover-rollout`; incomplete evidence remains `interrupted`.
4. For multi-stage routes, aggregate ordered receipts with `codex_exec_measure.py aggregate`. Never invent a quality result from receipt totals.
5. Run the same deterministic gate for every candidate, then use a route-blind evaluator. Candidate-authored tests against an old reference prove new-contract validation, not improvement.
6. Record normalized evidence in `run.json`. Include unsupported claims, invented paths or commands, false validation, skipped, blocked, failed, and validation evidence.
7. Compare matched runs with `compare_benchmark_runs.py`; use its optimization gate only across identical measurement boundaries with no quality, failure, skip, fallback, or hallucination regression.
8. Require at least three matched repetitions before automatic routing. A cost-first route needs no quality regression, median credits at most 25% of baseline, and median wall time at most twice baseline.

Use `prepare_benchmark_run.py` and `record_benchmark_result.py` for standard run packets. Use `three_arm_full_run.py` for isolated full-run evidence; bind exact prompts, external evaluation, provider-visible thread IDs, observed models, rehashed coordinator evidence, and deterministic `trial-index.json`. It prepares and aggregates but never launches agents. Delegated arms require complete direct-child thread-tree usage.

Use the capability matrix for missing/unsupported/supported command deltas; it is not a quality score. Artifact tokenizers measure artifacts, not provider bills. Subscription credits are consumption evidence, not a monetary invoice. Provider cost claims require an implemented trusted adapter; otherwise label current prices API-equivalent.

## Strict Read-Only Boundary

For strict read-only/offline/no-profile/no-temp/no-write dogfood, use only commands listed in `module.json.strict_read_only_commands` plus exact file reads. Do not prepare runs, create artifacts, inspect profiles, invoke agents or local AI, install, fetch, write outputs, or run broad validation.

Strict read-only/offline excludes write flags, live model/tool execution, local-AI writes, remote fetches, unscoped telemetry discovery, and any arm that creates a run folder or target artifact.

## Rules

- Separate cold and warm runs; pin CLI, model, reasoning, rate card, and quality gates.
- Compare only equal story, suite, expected checks, host/provider accounting, and token boundaries.
- Keep benchmark evidence under workflow `runs/`, never root-level runs.
- Treat `run.json` as evidence, not validation.
- Do not call a contaminated direct arm “no harness.”
- Do not claim a skill helps from one treatment; use paired no-skill/with-skill evidence.
- Promote recurring lessons only from repeated normalized evidence through validators, evals, or fail-fast checks.

## Validation

Normal validation only; skip it during strict dogfood.

```shell
python -B .agents/skills/agent-benchmarking/scripts/codex_exec_measure.py self-test
python -B .agents/skills/agent-benchmarking/scripts/run_self_tests.py
python -B .agents/manage.py eval-skill --skill .agents/skills/agent-benchmarking --suite .agents/skills/agent-benchmarking/suites/agent-benchmarking-evals.json --format json
python -B .agents/manage.py check
```

## Completion Contract

Report suite, task ID, run folder, subject, commands, outputs, token method, credits, cost availability, quality method, unsupported claims, evidence, and skipped/blocked/failed validation. If normalization fails, report the exact command and missing field.

## Stop Rules

- Stop before presenting estimates as exact usage or money.
- Stop before presenting artifact savings as provider-billed savings.
- Stop before comparing incomplete thread trees, different suites, or different validation boundaries.
- Ask before writing outside an explicit run or benchmark output folder.
- Fall back to read-only comparison when a run report is malformed.
