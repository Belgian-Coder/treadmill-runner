# Intake And Review

Use for creating, rewriting, reviewing, or promoting a skill. Use `$workflow-manager` for `automations/`.

Analyzer output is evidence, not judgment. Review a user-supplied or approved local copy; do not scrape remote URLs automatically.

## First Commands

```shell
python -B .agents/manage.py analyze-location <folder-or-file> [--format json] [--review-profile import]
python -B .agents/manage.py audit-candidate-source <folder> --summary --format json
```

`--review-profile import` checks hidden text, prompt-injection markers, likely secrets, disallowed scripts/hooks, committed tool settings, generated adapters, large/binary files, install/network signals, license/notice facts.

## Intake Record

Record skill name, goal, source path/URL/type, license/notice, dependencies, data transfer, version/quality facts, scope, overlap, decision.

Source focus:

- Docs/API: authority, freshness, auth, destructive calls, examples, limits.
- Git/codebase: license, scripts, manifests, CI assumptions, generated files, security posture.
- PDF/Office/media: conversion, layout fidelity, proprietary content, reusable examples.
- Existing skill: trigger accuracy, dependency drift, stale references, validation, one-job fit.

## Review Order

1. Analyze the local copy with `--review-profile import`.
2. Audit routing for unresolved `[skill:<id>]` references, missing invocation boundaries, cycles, high-similarity descriptions.
3. Check overlap against `.agents/routing.md` and accepted skill summaries.
4. Classify skill versus workflow before promotion.
5. Decide: reject, keep-staged, merge, rewrite-first, split, or promote.
6. Only then edit accepted skill files.

Imported skills are rewrite-first by default. Preserve useful facts/notices; rewrite behavior into this repo's trigger, workflow, guardrail, validation, and completion-contract shape.

## Skill Versus Workflow

- Reusable capability: `.agents/skills/<skill-name>/`.
- Pipeline with phases, run state, evidence, outputs: `automations/<workflow-name>/`.
- Repo policy/conventions: `AGENTS.md` or manager docs.
- Generated metadata/adapters: generated paths only.
- One-off notes/examples: keep staged, summarize, or reject.

## Decision Table

- `reject`: unsafe, unlicensed, too broad, or low value.
- `keep-staged`: useful but missing facts, validation, or ownership.
- `merge`: trigger overlaps an accepted skill; patch that skill.
- `split`: unrelated triggers/tools/risks bundled together.
- `rewrite-first`: useful external/generated source has wrong shape.
- `promote`: narrow, safe, validated, non-duplicative; add/validate manifest and sync artifacts.

## Rationalization Checks

- Reject skills for repo policy, one command, workflow phases, static docs, or accepted-skill variants.
- Failed validation is a source fact: read output, find first failing fact, patch one cause, rerun.
- Local AI may summarize deterministic evidence, not replace validation or owner review.
- Do not claim done, fixed, or passing without fresh command output.

## Quality Commands

```shell
python -B .agents/manage.py compare-skill --old <old-skill> --new <new-skill>
python -B .agents/manage.py eval-skill --skill <skill> --suite <suite.json>
python -B .agents/manage.py validate-agent-compatibility
python -B .agents/manage.py attest-skill --skill <skill> --format json
python -B .agents/manage.py skill-inventory --skill <skill>
python -B .agents/manage.py measure-skill-budget --skill <skill>
python -B .agents/manage.py sync
python -B .agents/manage.py check
```

Commands are local/offline except explicit source or credential behavior declared by the owning skill.
