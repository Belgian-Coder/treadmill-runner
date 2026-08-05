---
title: Project Policy Configuration
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-07-21
---

# Project Policy Configuration

Use the `policy` command to discover and tune human-facing limits, warnings, and command budgets without searching the implementation for constants. Configuration is validated before it is written, and every effective value reports whether it came from a built-in default or tracked project configuration.

## Quick Start

Inspect the current effective policy before changing it:

```shell
python -B .agents/manage.py policy list --section limits
python -B .agents/manage.py policy explain limits.agents.warn_chars
python -B .agents/manage.py policy validate
```

Create the complete tracked configuration and change one setting:

```shell
python -B .agents/manage.py policy init
python -B .agents/manage.py policy set limits.agents.warn_chars 3600
python -B .agents/manage.py policy reset limits.agents.warn_chars
```

`setup` creates `.agents/project-policy.json` when it is missing and refreshes its shape in write mode. `policy init`, `refresh`, `set`, and `reset` also write it atomically. The file contains the complete effective project policy: limits, warning actions, command budgets, and portable cost/context choices. This makes code review and direct JSON editing practical because no effective value is hidden behind a built-in default. Commit it with the consumer project; it is project-owned and preserved across harness updates.

Schema v2 is strict and self-describing through `$schema`. For an existing schema-v1 project, run `policy migrate` (or write-mode `setup`) once. Normal readers accept only v2; there is no legacy fallback that can silently discard project choices.

After installing a harness version that introduces new policy paths, materialize only those new defaults while preserving every existing choice:

```shell
python -B .agents/manage.py policy refresh --format json
```

## Discover Settings

Use `policy show` for the full catalog, `policy list --section <prefix>` for one family, and `policy explain <path>` for a single value with its description, unit, default, effective value, and source. JSON output is available for CI and editor integrations:

```shell
python -B .agents/manage.py policy list --section warnings --format json
python -B .agents/manage.py policy list --section commands.latency_ms --format json
python -B .agents/manage.py policy explain commands.output_tokens.status-fast --format json
```

The main families are:

| Family | Examples | Purpose |
|---|---|---|
| `limits.*` | `limits.agents.warn_chars`, `limits.skill.fail_words`, `limits.workflow.profile_text_chars`, `limits.navigation.source_snippet_chars` | Content-size, truncation, complexity, workflow, and navigation thresholds used by health checks, validators, and budget reports. |
| `warnings.*` | `warnings.health.skill.words`, `warnings.health.script.lines` | Actions for registered advisories: `off`, `warning`, or `error`. |
| `commands.latency_ms.*` | `commands.latency_ms.status-fast` | Total elapsed-time advisory budgets for measured commands. |
| `commands.component_latency_ms.*` | `commands.component_latency_ms.startup-context` | Slow-component advisory budgets inside a command. |
| `commands.output_tokens.*` | `commands.output_tokens.finish` | Compact-output advisory budgets. |
| `cost_policy.context.*`, `guidance.*`, `budgets.*`, `review.*`, `routing.*`, `delegation.*`, `local_ai.*` | `cost_policy.context.always_loaded.budget_tokens` | Portable local-first context, review, delegation, route, and fallback choices grouped by responsibility. |
| `output_profiles.*` | `output_profiles.compact_command.max_chars` | Reusable user-visible output retention profiles. |
| `owner_defaults.*` | `owner_defaults.repo_navigation.briefing.profiles.normal.item_limit` | Portable defaults owned by one skill or workflow but intentionally configurable by the project. |

Paths are intentionally explicit rather than accepting arbitrary keys. An unknown or missing path, wrong JSON type, out-of-range value, invalid warning action, or inconsistent pair is rejected without changing a file. `policy refresh` is the safe way to add newly registered defaults.

## Limits And Warning Actions

Limits determine when a registered check produces an advisory or hard failure. Warning actions control what happens to the corresponding advisory:

- `off` suppresses that advisory.
- `warning` reports it without failing the owning check.
- `error` promotes it to a check failure.

`warnings.default_action` applies to every registered end-user advisory that does not have a more specific stable warning ID. Specific `warnings.health.*`, `warnings.compatibility.*`, `warnings.workflow.*`, and `warnings.navigation.*` paths override it. This makes newly introduced semantic warnings configurable immediately while preserving independent severity for commonly tuned checks.

Hard fail limits remain failures; turning off a warning does not bypass them. For paired limits, the warning value must stay lower than the failure value. When raising both, raise the failure limit first:

```shell
python -B .agents/manage.py policy set limits.agents.fail_chars 5000
python -B .agents/manage.py policy set limits.agents.warn_chars 4500
python -B .agents/manage.py policy set warnings.health.agents.characters error
```

Restore any setting to its harness default while keeping it visible in the complete document:

```shell
python -B .agents/manage.py policy reset warnings.health.agents.characters
```

## Command Budgets

Command latency and output limits are advisory performance budgets; they do not terminate a running command. Project configuration overrides the built-in budget. A command implementation that explicitly supplies a per-call budget still takes precedence because it has more specific runtime context.

```shell
python -B .agents/manage.py policy set commands.latency_ms.status-fast 6000
python -B .agents/manage.py policy set commands.output_tokens.status-fast 2400
python -B .agents/manage.py command-budget-check --summary --compact --format json
```

## Local-AI Cost Policy

The `cost_policy.*` tree is project policy and therefore lives in `.agents/project-policy.json`. The local-AI catalog and allowed task IDs remain in `.agents/local-ai.json`; `policy set` validates cost/context relationships against that catalog before writing:

```shell
python -B .agents/manage.py policy explain cost_policy.context.always_loaded.budget_tokens
python -B .agents/manage.py policy set cost_policy.context.always_loaded.budget_tokens 5500
python -B .agents/manage.py cost-policy --check --summary --compact --format json
```

Machine-specific GPU, runtime, thread, and installed-model choices do not belong in project policy. Configure them with `local-ai configure`; local scope writes gitignored `.agents/local-ai/local.settings.json`. Catalog profile selection and bounded runtime routing remain with the local-AI project settings owner because they require model-catalog validation.

## What Belongs Here

Move a setting into project policy when it is portable, team-owned, and changes a validation result, warning severity, user-visible truncation, review/context budget, or deterministic operating preference. Examples include skill and documentation limits, workflow profile and context-packet lengths, navigation map and snippet limits, routing summary length, feedback text retention, compact command/path lengths, command budgets, review-loop budgets, and cost/context routing policy.

Keep a setting with another owner when it describes machine hardware, credentials, absolute paths, ports, installed models, GPU/runtime calibration, or a domain-specific validated catalog. Keep protocol widths, hash lengths, parser look-behind windows, bounded evidence sample counts, streaming chunk/overlap sizes, archive and source-read ceilings, and algorithmic ranking constants in code when changing them would not express project intent. These are implementation or safety invariants, not end-user policy; they are intentionally absent from the central file.

## Fixed Safety Boundaries

The catalog excludes non-bypassable safety ceilings and permissions. End users cannot configure away path traversal checks, safe archive extraction, credential protections, symlink or reparse-point rejection, transaction integrity, model catalog validation, download confirmation, or absolute resource-exhaustion safeguards. A bounded operational value such as `limits.skill.asset_max_bytes` may be lowered, but it cannot exceed its fixed safety maximum.

## Review And Validation

Review the scoped change with `changed-context`, `review-packet`, or `review-loop` before opening any broader Git diff, then run:

```shell
python -B .agents/manage.py policy validate --format json
python -B .agents/manage.py status --fast
python -B .agents/manage.py check
```

Direct JSON edits are supported when they follow `.agents/skills/skill-manager/assets/schemas/project-policy.schema.json`, but the CLI is preferred because it runs both structural and cross-field semantic validation before atomically replacing the owner file. File lists must contain unique, normalized, repository-relative POSIX paths; absolute paths, backslashes, and traversal are rejected.
