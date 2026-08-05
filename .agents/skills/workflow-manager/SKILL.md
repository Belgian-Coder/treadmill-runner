---
name: workflow-manager
description: Use when creating, reviewing, validating, routing, or resuming automations/ workflow modules with module.json contracts, WORKFLOW.md entry files, deterministic commands, generated catalogs, and workflow-owned evidence packets.
---

# Workflow Manager

## Goal

Maintain `automations/` contracts, entries, phases, suites, hooks, and run packets; reusable capability belongs in `$skill-manager`, orchestration here.

## Workflow

1. Route with `automations/routing.md`; open the selected owner entry files and directly relevant subpaths.
2. For strict read-only/offline/no-profile/no-temp/no-write dogfood, use docs and exact `rg`; run only read/check/dry-run commands, scorecard `--no-lifecycle`, or smoke `--dry-run`.
3. Read `docs/workflow/workflows.md` for design rules and command paths.
4. Before creating, run `workflow propose --from-request "<request>"`; choose new/extend/none and confirm boundaries.
5. Scaffold with `workflow create --from-request "<request>" --name <workflow-name> --write` or the manual command below; customize generated `WORKFLOW.md`, `instructions.md`, `module.json`, diagrams, and eval suite.

```shell
python -B .agents/manage.py new --kind workflow --name <workflow-name> --summary "<summary>" [--uses-skill skill-manager --uses-script ".agents/manage.py compare-skill"]
```

6. Small layout: `WORKFLOW.md`, `module.json`, optional `instructions.md`, `diagrams/`, `scripts/`, `templates/`, `suites/`, fixtures, active `runs/`.
7. `module.json` owns phases, tasks, workers, hooks, risk, access, inputs/outputs, commands, modules, validation, local AI, optional schema/gates/templates/branch policy; lifecycle state stays in `run.json`.
8. Evidence stays under `runs/<run-id>/run.json`, `REPORT.md`, `validation/`, or `artifacts/`. Verify accepted screenshots with `workflow validation-packet`; record skips and avoid retained dogfood runs.
9. Validate/sync routing:

```shell
python -B .agents/manage.py validate-automations --name <workflow-name> --strict-phase-quality
python -B .agents/manage.py workflow template gate-check --all --format json
python -B .agents/manage.py workflow hooks --all --check --format json
python -B .agents/manage.py eval-workflow --name <workflow-name> --suite automations/<workflow-name>/suites/workflow-evals.json
python -B .agents/manage.py workflow scorecard --name <workflow-name> --format json
python -B .agents/manage.py sync-automation-routing
python -B .agents/manage.py check-additions
python -B .agents/manage.py check
```

Strict equivalents: `python -B .agents/skills/workflow-manager/scripts/workflow_repo_manager.py validate-automations --root . --name <workflow-name> --strict-phase-quality --format json`, `workflow scorecard --no-lifecycle`, `workflow smoke --dry-run`, and `sync-automation-routing --check`.

More lifecycle/hook/worker/smoke/context/analytics/run-index commands: `docs/reference/commands.md`.

## Rules

- No workflow for one command, static docs, repo policy, variants, or reusable capability.
- Reference reusable skills through `related_modules`, `commands`, extension points; do not copy full skill instructions.
- Strict read-only/offline/no-profile/no-temp/no-write excludes lifecycle writes, unchecked sync, executable fixtures, external/profile/credential inspection, and tempfile/subprocess/cleanup-backed tools.
- Keep generated routing, `agents/openai.yaml`, root-level `runs/`, wrappers, command files, `__pycache__`, and undeclared optional files out of active paths.
- New/changed workflows must pass addition acceptance, Mermaid syntax, evals, scorecard, generated routing, repo `check`.
- Every workflow needs Azure-compatible process/connection diagrams; add low-level diagrams for multi-module/service/generated-artifact work. Data changes need an `erDiagram` or skip reason.
- Failed validation: read output, find the first failing fact, patch one cause, rerun.
- Completed run packets must record evidence entries or evidence paths, unsupported claims, external validation status; `not-recorded` is not finishable.
- Use optional task graphs only for larger workflows; runtime task status stays in `run.json`.
- Use portable `module.json.worker_profiles` as declarative/manual guidance. Choose `serial`, parent-owned `direct-child-agent`, or explicitly user-requested `independent-thread`; serial is the fallback. Route by consequence, reversibility, scope clarity, and repeated failure—not model nicknames. Require requested/observed model and thread attestation plus the economics gate. Low-cost coordinators cannot own architecture, security, migrations, or final acceptance. Parallel work requires stable inputs, independent work, and no shared write targets. See [Model Compatibility And Routing](../../../docs/reference/model-compatibility-and-routing.md) and [Delegation And Parallel Safety](../../../docs/reference/delegation-and-parallel-safety.md).
- Scaffolded `--uses-skill`/`--uses-script` values point to accepted skills, known `.agents/manage.py` commands, or existing Python scripts.
- Scripts use Python 3.12+ stdlib and run with `python -B` or `PYTHONDONTWRITEBYTECODE=1`.
- Keep workflow JSON two-space pretty-printed; run `python -B .agents/manage.py format-json` after JSON edits.
- Root docs under `docs/**/*.md` keep frontmatter and material `updated` changes.
- Run scorecard and fixture-backed `workflow smoke` sequentially; skip live Azure DevOps, SonarQube, CI with reasons. Promote proof into durable assets.
- Outputs stay under the module; target-project effects, installs, commits, writes must be declared.
- Resolve/lint templates through workflow commands. Provider order: project overrides, presets, lean templates, defaults.
- After `module.json`/`metadata_path` edits, run `workflow metadata inspect`; before managed/generated section replacement, run `workflow managed-section-diff`; describe integrations in `integration.json`, then run `workflow integration-check`.
- Context packets pass `workflow context --all --check --summary --compact --format json`; preserve exact resume IDs. Persist model identity only from a strict validation packet via `workflow context --runtime-observation-file <path> --write`; it must match workflow, run, and phase. Phase changes clear the active record but retain evidence. Lifecycle reads revalidate persisted evidence; missing, changed, or stale evidence uses the generic overlay.
- Natural-language workflow routing is start-ready only at high confidence. Medium confidence requires a more specific request or explicit user/agent confirmation; use `start_command_if_confirmed` only after that confirmation.
- Use `workflow branch-policy` before commit/PR handoff; it reports branch naming only.
- Optional setup/install reports skipped/failed setup and continues only for declared non-blocking behavior.
- Local AI is advisory. User-facing lifecycle commands are `workflow start`, `workflow resume`, and `workflow finish`.
- Workflows declare required context evidence in `module.json`; lifecycle commands write `validation/context-evidence-<event>.*`. Context-evidence failure blocks only when declared files do not prove the required context.

## Extension Points

Bind skill extensions only through `module.json`: workflow-local inputs, declared outputs, Python 3.12+ stdlib wrappers under `scripts/`, hooks under `runs/<run-id>/validation/hooks/`.

## Validation

Use step 9. For workflow edits, run `check-additions`, `validate-automations`, template resolve/lint, integration checks, `sync-automation-routing`, worker checks, scorecard/smoke, `index-workflow-runs --name <workflow-name> --check` when `runs/` exists, and repo `check` unless blocked.

## Completion Contract

Report low-context files used/skipped, workflow path, contracts, related modules/commands, outputs, generated routing, validation, evidence, skipped/blocked/failed checks, risks, and `Workflow used: <path> - <reason>`/`Skill invoked: <name> - <reason>` when material.

## Stop Rules

Stop before finalizing when routing, generated files, declarations, outputs, run indexes, or validation evidence are stale/missing/failing.
