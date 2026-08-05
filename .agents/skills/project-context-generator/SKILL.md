---
name: project-context-generator
description: Use when generating or refreshing workflow-ready project context files from an existing project, including structure, responsibilities, technologies, .NET project context, security notes, validation commands, and proof scripts with optional Playwright screenshot evidence.
---

# Project Context Generator

## Goal

Generate a durable `docs/project/project-context.md` package that workflows can load before planning user stories, bugs, migrations, upgrades, and validation work.

## Workflow

1. For read-only/offline dogfood, use the skill/module files, CLI `--help`, and exact stdout-only commands; do not open generated registries or raw navigation JSON. Generator inspection without `--write` is stdout-only but still reads local target project files, so point `--target` at the narrow app/project root. Safe strict commands:

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/project-context-generator --summary --compact --format json
python -B .agents/manage.py inspect-skill --skill .agents/skills/project-context-generator --fast --summary --compact --format json
python -B .agents/manage.py measure-skill-budget --skill project-context-generator --summary --compact --format json
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target <project-root> --format json
```

Run `eval-skill` only when temporary fixture writes are allowed, because this suite executes self-tests. Do not run the validation runner, self-tests, screenshots, `--write`/`--overwrite`, or `measure-skill-budget --write-trend` when no files may be created. Risk profile is local-write for full operation; strict dogfood is only the exact read-only subset above.
2. Inspect the narrow app/project root before writing context. Avoid broad repository-root scans on large multi-project repos unless the repo root is the actual target project.
   - For active `.sln`, `.slnx`, or SDK-style project files, the generator calls `dotnet-project-context` in static mode to enrich SDK/runtime, build policy, CI candidates, config key inventory, persistence signals, NuGet/feed policy, and .NET validation-candidate facts without restore/build/test execution.
3. Generate context from the target project when workflow-owned writes are allowed:

```shell
python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target <project-root> --write --format markdown
```

4. Review generated assumptions in `docs/project/project-context.md`, `docs/project/project-context.json`, and `docs/project/validation/validation-manifest.json`.
5. Run the generated validation runner from the target project when workflow evidence needs build/test proof. This always writes a timestamped evidence report under `--evidence-dir`, including when `--list` is used:

```shell
python -B docs/project/validation/run_project_validation.py --target . --evidence-dir docs/project/validation/evidence
```

6. For UI proof, start the app with the project’s normal command, then pass a URL for an optional Playwright screenshot. This writes screenshot logs/artifacts under the evidence run folder:

```shell
python -B docs/project/validation/run_project_validation.py --target . --screenshot-url http://localhost:3000 --evidence-dir docs/project/validation/evidence
```

7. Attach the JSON report, markdown summary, command logs, copied Playwright artifacts, and screenshots to workflow run evidence.

## Rules

- Use deterministic file/package/project inspection first; do not emit secret values or infer private service details.
- Write only under the selected target project, normally `docs/project/`; the generator rejects output directories that resolve outside the target root.
- Do not install packages, browsers, SDKs, or tools. The validation runner uses existing project commands and records missing tools as blocked/skipped evidence.
- Strict read-only/offline excludes `--write`, `--overwrite`, validation runner execution, `--evidence-dir` reports, `--screenshot-url`, `eval-skill` suites that execute self-tests, self-tests that create temp fixtures, and generated `docs/project` files. Use stdout-only generator inspection instead.
- Missing package managers, SDKs, Playwright, screenshots, setup commands, failed commands, or install prerequisites are reported as skipped, failed, or blocked evidence; workflows may continue only when those checks are explicitly non-blocking for the current request.
- Keep generated context workflow-ready: include project purpose, technologies, structure, responsibilities, security/configuration notes, validation commands, Playwright evidence path, workflow usage notes, and freshness metadata.
- Keep `.NET` sections compatible with `dotnet-project-context`; private/internal feeds, config keys, CI commands, and persistence signals are review evidence, not proof that restore/build/test can run.
- Use materialized `.mmd` plus dark transparent `.svg` diagrams; do not commit live Mermaid blocks in durable context docs.
- Treat generated context as current evidence but still report assumptions and low-confidence inferences for review.
- `python -B .agents/manage.py setup` may invoke this generator after `repo-navigation` map initialization; keep outputs compatible with `repo-navigation project-context --check`.
- Do not overwrite an existing reviewed project context from setup. Write the whole generated package under a sidecar directory unless `--overwrite` is explicit or the context is a copied source-harness context in a consumer install.
- Hand off to `$repo-navigation` for compact one-off repo briefs and to `$playwright-integration` for Playwright setup/readiness diagnostics beyond command execution.

## Validation

```shell
python -B .agents/skills/project-context-generator/scripts/run_self_tests.py
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/project-context-generator
```

## Completion Contract

Report target root, context files written, detected technologies, `.NET` context status when present, generated validation commands, generated diagrams, Playwright screenshot support, validation evidence paths, skipped/blocked checks, failed commands, and remaining assumptions.

Report `Skill used: project-context-generator - <reason>` when this skill materially affected the work.

## Stop Rules

Stop before overwriting an existing reviewed project context unless `--overwrite` is explicitly requested. Stop before running generator writes or validation evidence writes under strict read-only/offline constraints. Stop before running installs or browser downloads. Stop before claiming validation passed unless the generated validation report has passing command evidence.
