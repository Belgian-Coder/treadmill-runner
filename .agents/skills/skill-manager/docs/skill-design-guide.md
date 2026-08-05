# Skill Design Guide

Use before creating, rewriting, promoting, or upgrading `.agents/skills/`. Re-check official model and Agent Skills docs before major format or routing changes.

## Principles

- One job; split broad candidates.
- `description` is the routing API; name nearby non-goals.
- Keep `SKILL.md` procedural; move background, examples, and edge cases to `docs/`, `assets/`, or scripts.
- Declare network, credentials, uploads, installs, destructive actions, production writes, generated settings, and third-party transfer.
- Helpers use Python 3.12+ stdlib, explicit inputs/outputs, `--check` or clear write intent.
- Skill `assets/` files stay <=5MB; shrink bulky examples before promotion.
- Routing, registries, Claude, and Copilot surfaces come from `sync`.
- New files pass `check-additions` through an owner, contract, or generated-source relationship.
- .NET Framework belongs to `dotnet-legacy`; modern `dotnet-*` skills hand off Framework-era maintenance.
- Final reports name changed paths, commands, validation, skipped/blocked/failed checks, risks.

Validation/analyzer scripts emit `schema_version`, `tool`, `ok`, `status`, `summary`, `checks`, `skipped`; cap success logs and mark estimates.

## Accepted Skill Shape

Required:

```text
.agents/skills/<skill-name>/
  SKILL.md
  module.json
  suites/
```

Optional: `docs/`, `scripts/`, `assets/`.

`SKILL.md` covers goal, workflow, guardrails, validation, completion contract, stop/fallback rules. Frontmatter contains only:

```yaml
---
name: skill-name
description: Use when ...
---
```

Folder name, frontmatter `name`, and `module.json.id` must match. Names use lowercase letters, digits, hyphens. Keep `SKILL.md` UTF-8 without BOM. Docs need H1 titles, resolved unfenced `[skill:<id>]` references, and no `## Scope` or `## Out Of Scope`; keep boundaries in `SKILL.md`.

## module.json

Accepted skills use schema version `2`, `kind: skill`, matching `id`, SemVer `version`, `status`, compact `summary`, Codex/Copilot/Claude compatibility, dependencies, risk, quality/eval facts, provenance.

Bump version for behavior, dependency, risk, metadata, or docs changes; do not hide dependency/risk changes in a docs-only bump.

## Extension Points

Add `## Extension Points` only when workflows adapt a skill with local files, templates, rules, or wrappers. Name accepted inputs, output/evidence shape, allowed wrappers, and stop rules for missing inputs, undeclared scripts, credentials, installs, network calls, writes.

Skills must not scan hooks, import workflow Python modules, or run workflow scripts unless the active workflow passes them.

## Intake And Promotion

Record skill name, user goal, source, license/notice, dependencies, data transfer, risk, scope, version/quality facts, split candidates.

```shell
python -B .agents/manage.py analyze-location <folder-or-file> --review-profile import
```

Promote only narrow, safe, validated, non-duplicative skills with compact context and refreshed generated files. Imported/generated candidates are rewrite-first: preserve useful facts/notices, rewrite behavior into repo style.

## Upgrade Review

Compare before replacing active skills:

```shell
python -B .agents/manage.py compare-skill --old <old-skill> --new <new-skill>
python -B .agents/manage.py upgrade-skill --old <old-skill> --new <new-skill> --target .agents/skills/<skill-name> --strategy override
python -B .agents/manage.py audit-candidate-source <candidate-root> --summary --format json
```

Use `inspect-skill`, `eval-skill`, `validate-agent-compatibility`, `attest-skill`, `skill-inventory`, and `measure-skill-budget` when facts affect the decision. Use `inspect-skill --deep` for risk evidence, hashes, full-text scanning.

## Repository Rules

- Use `$workflow-manager` for `automations/<workflow-name>/`.
- Keep per-skill docs under `docs/`; avoid per-skill `README.md` unless requested.
- Keep IDE setup optional, capability-detected, non-blocking, and owned by the using skill.
- Do not add committed tool settings, trust entries, shell wrappers, or hidden setup.
- Ask only questions that change behavior or risk; otherwise use low-risk defaults and report assumptions.

After active skill or workflow changes:

```shell
python -B .agents/manage.py check-additions
python -B .agents/manage.py sync
python -B .agents/manage.py check
```
