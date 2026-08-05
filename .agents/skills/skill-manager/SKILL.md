---
name: skill-manager
description: Use when managing repository skills or the reusable harness for creation, import, review, upgrade, versioning, installation/update, validation, inventory, budgets, routing, or adapter sync.
---

# Skill Manager

## Goal

Validate skills; route workflows to `$workflow-manager`.

## Workflow

1. Route with `.agents/routing.md`; open the selected owner.
2. For strict no-write/no-temp/no-profile offline dogfood, use only declared strict commands. Report stale generated files without writing.
3. For create/import/promote/upgrade, read this skill's design and intake docs.
4. Analyze copied/generated/external candidates:

```shell
python -B .agents/manage.py analyze-location <folder-or-file> [--format json] [--review-profile import]
python -B .agents/manage.py audit-candidate-source <folder> --summary --format json
```

5. Check overlap, classify skill vs workflow, then decide `reject`, `keep-staged`, `merge`, `split`, `rewrite-first`, or `promote`.
6. Shape accepted skills around goal, workflow, guardrails, validation, completion, stop/fallback, eval coverage.
7. Route workflow candidates/extensions/generated sections/integrations through `$workflow-manager` before promotion:

```shell
python -B .agents/manage.py workflow template lint --name <workflow-name>
python -B .agents/manage.py workflow integration-check --format json
python -B .agents/manage.py workflow managed-section-diff --target <file> --replacement <file> --format md
```

8. Use compact facts first:

```shell
python -B .agents/manage.py inspect-skill --skill .agents/skills/<skill-name> --fast
python -B .agents/manage.py review --skill .agents/skills/<skill-name> --plan
python -B .agents/manage.py measure-skill-budget --skill <skill>
```

Outside strict dogfood, check local setup before live calls. If config is missing, ask for required details and write the gitignored profile; never guess URLs, keys, or token sources:

```shell
python -B .agents/manage.py credential-doctor --summary --compact --format json
python -B .agents/manage.py credential-doctor --configure --service sonarqube --name project-a --base-url https://sonar.example --project-key ProjectA --token-env SONAR_TOKEN --format json
```

9. Validate and sync routing/adapters:

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/<skill-name>
python -B .agents/manage.py check-additions
python -B .agents/manage.py sync
python -B .agents/manage.py validate-agent-compatibility
```

Outside strict dogfood, use `determinism-check --changed`; release validation uses `--all --deep` and blocks unresolved placeholders.

See `docs/reference/commands.md` for inventory, comparison, determinism, adapters, and deep checks. For dirty worktrees use `check-changed [--deep]`. Local AI setup stays in `$local-ai-helper`; deterministic evidence and exit codes outrank advice.

Harness consumers keep one Git repository and tracked `.agents/harness.lock.json`. Updates are previewed, confirmed, transactional, and rollback-capable; `harness-adopt` converts the legacy manifest once. Tracked overlays transfer paths to project ownership; other edits collide. Promote reusable improvements from a sibling clone, never overlays or local settings.

Teams manage tunable limits, warnings, budgets, and portable cost/context policy with `policy list/explain/set/reset`. Tracked `.agents/project-policy.json` is complete and reviewable; `policy refresh` adds new defaults without overwriting choices. Machine local-AI settings and absolute safety ceilings remain outside it.

## Reviewed Corrections To Evals

Convert owner-reviewed corrections into provider-neutral eval candidates:

```shell
python -B .agents/manage.py feedback review-digest --corrections <corrections.json> --format json
python -B .agents/manage.py feedback eval-packet --corrections <corrections.json> --output <eval-packet.json>
```

Follow `assets/schemas/correction-events-v1.schema.json`: use `review-input`, run the digest, then set `reviewed` and copy its reviewer-bound digest. Validate before candidate promotion.

## Rules

- Reject broad personas, always-on triggers, hidden network calls, unreviewed generated content, unclear dependencies, undeclared risk.
- Writes, destructive actions, credentials, uploads, APIs, installs, and third-party transfer require explicit approval.
- Strict read-only/offline permits only declared strict commands; skip writes, temp/cache/progress, credentials/profiles, workflow/local-AI/smoke/eval/self-tests, and broad checks.
- External service configuration is local-only and gitignored in `.agents/local-ai/secrets.local.json`; prefer token environment variables unless local storage is explicitly accepted.
- No skill for repo policy, one command, static docs, workflow phases, variants, or behavior better expressed as a workflow contract.
- Imported/generated candidates are rewrite-first: preserve useful facts/notices; rewrite behavior before promotion.
- Keep canonical behavior under `.agents/skills`; bind workflow-specific behavior only through declared extension points.
- Workflow-adjacent skills hand off through declared module extension points; never copy workflow behavior into `SKILL.md`.
- Harness updates pin annotated stable tags to commits, run no consumer Git commands, preflight all collisions, write the lock last, retain ignored rollback transactions, and never force-overwrite.
- Run `workflow managed-section-diff` before replacing managed/generated sections; update through the owning generator or sync path.
- Use Markdown unless JSON improves validation/interoperability; preserve `LICENSE.txt` and `NOTICE.txt`.
- Add files only for maintenance, validation, or discovery; `check-additions` requires an owner, contract, or generated-source relationship.
- `SKILL.md` must be BOM/control-free. Docs need H1 titles, resolved unfenced skill references, and clear scope.
- Skill `assets/` files stay <=5MB each; bulky examples stay staged until reduced.
- Keep accepted layout flat: `.agents/skills/<name>/SKILL.md`.
- .NET Framework work hands off to `dotnet-legacy`.
- On failure, isolate the first fact, reproduce if feasible, patch one cause, and rerun.
- Use Python 3.12+ stdlib scripts; run maintained commands with `python -B` or `PYTHONDONTWRITEBYTECODE=1`.
- Do not keep `__pycache__` in active paths.
- Keep tracked JSON two-space formatted with a trailing newline; run `format-json` after edits.
- Root docs need metadata frontmatter, documentation-map reachability, and supported commands only.
- Normal work must not ask users to run internal local-AI commands; owning commands trigger required evidence and validation.
- `cost-policy` prioritizes local AI, reports prompt controls and warm-batch guidance; advice stays non-authoritative.
- IDE setup belongs to the owning skill; report skipped/failed optional setup as non-blocking when core work can proceed. Do not commit settings.
- Keep `SKILL.md` lightweight for small models; move examples and edge cases to docs, assets, or scripts.

## Validation

Use workflow step 9. For source edits, run addition acceptance, sync, and repo `check` unless blocked.

## Extension Points

Workflow-adjacent changes bind through workflow `module.json`, descriptors, managed-section diffs, and workflow-manager validation. Inputs: candidate/skill folders and snippets. Outputs: accepted skill files, workflow metadata, validation reports, generated routing/adapters from `sync`. Stop if hidden installs, undeclared external access, generated-file hand edits, or copied workflow behavior is required.

## Completion Contract

Report low-context routing used/skipped, inspected skill paths, Q&A asked/skipped, decision, commands, generated artifacts, validation, skipped/blocked/failed checks, risks, and `Skill used: <name> - <reason>` when material.

## Stop Rules

- Keep candidates staged while paid/discontinued services, hidden uploads, unsafe scripts, platform-specific behavior, or undeclared risk remain.
- Stop if generated skill routing, Claude adapters, compatibility, or validation are stale/failing.
