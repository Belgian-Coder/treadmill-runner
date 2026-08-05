---
name: external-reference-manager
description: Use when cloning, fetching, pinning, refreshing, or summarizing external Git references for workflows, including Azure DevOps repositories and compact reference cards.
---

# External Reference Manager

## Goal

Keep external code projects fast to use and reproducible by refreshing Git mirrors on demand, pinning exact commits, and generating compact reference cards for agents.

## Workflow

1. Read a reference manifest with a `references` array. Each item declares `name`, `repository_url`, `path`, and either `branch`, `tag`, or `commit`. Creating the manifest from the example is write-capable and only allowed when workspace writes are approved.
2. Keep local repository mirrors out of normal execution. Use the `reference-refresh` workflow unless the user explicitly asks to refresh references.
3. Run the report first. The default is read-only and reports stale pinned age, local upstream divergence, changes since last pin, and missing/deleted/renamed referenced files detectable from local mirrors, cards, and manifests.
4. Run a dry-run before writing when refreshing live references. It lists clone/update, pin, and card changes without fetching or writing.
5. Refresh only with explicit `--write`. Use `--no-fetch` for fixtures or already-cloned repositories; use normal fetch mode for live Azure DevOps references.
6. Review changed pins, stale-reference age, upstream divergence, and “what changed since last pin” before relying on reference patterns.
7. Copy only compact observations into story or bug plans. Do not paste large reference files into workflow state.

```shell
python -B .agents/manage.py reference-refresh --mode report --format markdown
python -B .agents/manage.py reference-refresh --mode dry-run --no-fetch --format json
python -B .agents/manage.py reference-refresh --mode write
```

Local AI may summarize the deterministic report or generated reference cards only after the sync script has produced them:

```shell
python -B .agents/manage.py local-ai task --task inventory-summary --input <report-or-card>
```

Fallback without local AI: use the deterministic report, then read `pinned-references.json` and compact card Markdown files when present.

## Read-Only Dogfood

For strict read-only/offline review, use routing reads, docs, help, `inspect-skill --fast`, `validate_skill.py`, `module.json.strict_read_only_commands`, inspect eval suites without executing them, report mode, and dry-run mode with `--no-fetch`. Report mode is read-only; it inspects only local manifests, mirrors, pins, and cards. Dry-run mode does not fetch or write; it reports clone, fetch, pin, and card intent. Prefer source-reviewed direct script commands.

```shell
python -B .agents/skills/external-reference-manager/scripts/sync_references.py --manifest automations/reference-refresh/artifacts/references/reference-manifest.json --output-root automations/reference-refresh/artifacts/references --workspace-root . --no-fetch --format json
python -B .agents/skills/external-reference-manager/scripts/sync_references.py --manifest automations/reference-refresh/artifacts/references/reference-manifest.json --output-root automations/reference-refresh/artifacts/references --workspace-root . --dry-run --no-fetch --format json
```

Self-tests use temporary Git fixtures; skip them under strict no-temp constraints. Manifest creation from the example is always a write and must be skipped in strict dogfood; a missing active manifest is a valid skipped terminal state. Skip `--write`, live fetch, `--allow-reset`, cleanup, local AI, workflow lifecycle, sync without `--check`, credential setup, manifest creation, and publishing unless explicitly approved. Report skipped/failed optional Git/network setup as non-blocking; continue with deterministic local facts.

## Rules

- Do not reset a dirty reference checkout unless `--allow-reset` is explicitly provided.
- Do not store Git credentials in the manifest. Use the local Git credential manager or environment-supported authentication.
- Pin every refreshed reference to a commit hash.
- Store generated cards under the reference-refresh output folder, not under story or bug workflow run folders.
- Skill manifest outputs stay empty because pins and cards are caller-owned workflow or project evidence, not durable skill-owned artifacts.
- Prefer narrow reference cards over broad source dumps.
- Treat local AI output as advisory over the generated deterministic report; do not use it as the source of truth.
- Cleanup local mirrors only when refreshing references; keep committed cards and pins as the portable source of truth. Removing obsolete mirrors is safe only when no manifest entry points at them.
- If a card references upstream files that were renamed or deleted, or report-only mode flags a card integrity mismatch, report the conflict and refresh the card before using that pattern.

## Validation

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/external-reference-manager
python -B .agents/skills/external-reference-manager/scripts/run_self_tests.py
```

Run self-tests only when temporary Git fixture writes are allowed.

## Stop Rules

- Stop if a reference repository has local changes and `--allow-reset` was not provided.
- Stop if the manifest path or output path escapes the current workspace.
- Stop if a reference cannot be pinned to a commit.

## Completion Contract

Report the manifest used, references refreshed, pins before and after, cards generated, validation status, failed fetches, skipped references, blocked dirty repositories, and remaining drift risks.

Report `Skill used: external-reference-manager - <reason>` when this skill materially affected the work.
