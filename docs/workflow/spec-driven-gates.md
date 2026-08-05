---
title: Spec Driven Workflow Gates
type: reference
status: active
owner: workflow-manager
audience: agent
updated: 2026-06-05
---

# Spec Driven Workflow Gates

Story, bug, and disciplined-change plans include spec-driven quality gates before implementation approval. These gates adapt the useful parts of GitHub Spec Kit's clarification, checklist, analyzer, task slicing, bug-stage, and template layering patterns into this repo's existing workflow model.

The repo uses these additions as workflow-manager structure, not as a catalog install policy. Metadata guides validation and routing; commands still make writes, installs, commits, and external calls explicit.

## Required Gates

Use these sections in `plan.md` before approval:

| Section | Purpose | Finish Signal |
|---|---|---|
| Clarification Decisions | Ask at most five targeted questions when scope, criteria, integration, or completion signals are ambiguous. | Every ambiguity has a decision, owner, evidence, and status, or one explicit no-clarification-needed row. |
| Workflow Inputs And Gates | Record typed inputs and human/deterministic gates. | Required gates are passed, pending for approval, or blocked with evidence. |
| Requirements Quality Checklist | Treat requirements as testable text before implementation tests exist. | Clarity, measurability, independent testability, assumptions, edge cases, and non-functional expectations are checked. |
| Cross-Artifact Coverage Analysis | Compare ticket/request facts, plan items, task rows, and validation. | Every requirement or owner decision maps to planned work and validation or a named follow-up. |
| Principles And Complexity Gate | Keep smallest-correct-vehicle and deterministic-validation decisions explicit. | Complexity has a reason, rejected simpler alternative, and evidence. |
| Template And Extension Layering | Make overrides and extension points visible. | Project override, workflow template, extension, and core default decisions are recorded. |

## Story Plans

Story plans also include `Story-Sliced Task Plan`. Group work by independently testable story outcome. Mark a task `[P]` only when it can run in parallel without shared write targets or unfinished dependencies.

## Bug Plans

Bug plans also include `Assess Fix Test Boundaries`.

| Stage | Write Boundary |
|---|---|
| Assess | Workflow evidence only. No source edits. |
| Fix | Approved affected source files plus workflow evidence. Deviations must be logged before continuing. |
| Test | Validation evidence only. No source edits unless the plan returns to the fix stage. |

## Parser Contract

`workflow plan-check` validates these sections for run plans in `user-story-workflow`, `bug-ticket-workflow`, and `disciplined-change-workflow`. Template checks validate section presence but do not require filled rows. Run checks require filled evidence rows and add issues to the fix queue.

The gates strengthen existing workflow evidence; they do not replace deterministic build, test, lint, security, documentation, or finish checks.

## Workflow Metadata

Use optional `metadata_path` in `module.json` to keep workflow contracts compact while preserving typed metadata for validators. The target JSON lives inside the workflow, usually `metadata/workflow-metadata.json`, and can contain:

| Field | Purpose |
|---|---|
| `input_schema` | Declares named request inputs such as `run_id`, `project_root`, work-item id, approval state, or selected vehicle. |
| `gates` | Declares reusable gate ids, types, summaries, required state, and evidence sections. |
| `template_layers` | Declares default template, project override roots, and preset roots. |
| `branch_policy` | Declares the branch-name regex used by the explicit validator. |
| `integrations` | Names related integration descriptors without implying install or network behavior. |

Validation accepts these fields in `module.json` or the referenced metadata file and checks shape, known gate/input types, unique gate ids, and branch regex validity. `workflow metadata inspect --name <workflow>` shows the merged `module.json` and `metadata_path` view. `workflow plan-check` also reads required gates from the referenced metadata file and verifies their evidence sections.

## Template Layers

Resolve templates through:

```shell
python -B .agents/manage.py workflow template resolve --name <workflow-name> --template plan.md
python -B .agents/manage.py workflow template resolve --name <workflow-name> --template plan.md --profile lean
python -B .agents/manage.py workflow template lint --name <workflow-name>
python -B .agents/manage.py workflow metadata inspect --name <workflow-name> --format json
```

Resolution order is project override, workflow preset by numeric priority, lean template when requested, then workflow default. Overrides belong under `docs/project/workflow-overrides/<workflow>/`; presets live under `automations/<workflow>/presets/<preset>/` with `preset.json` plus `templates/`; lean templates live under `automations/<workflow>/templates/lean-*.md`.

`workflow template lint` reports missing required plan templates, duplicate priorities, invalid preset manifests, and non-Markdown template providers.

## Integration Descriptors

Integration descriptors live at `integrations/<id>/integration.json` or `.agents/integrations/<id>/integration.json` and are checked with:

```shell
python -B .agents/manage.py workflow integration-check --format json
```

The descriptor root uses `schema_version: 1`, an `integration` object with `id`, `name`, `version`, `description`, `owner`, and `license`, and optional `provides.commands`, `provides.managed_files`, and `provides.tools` lists. The id must match the folder name and the version must be SemVer. Workflows that declare `integrations` must reference matching descriptor ids.

## Managed Sections And Branch Policy

Use `workflow managed-section-diff` before replacing managed instruction sections or generated adapter blocks. The command is read-only and prints the proposed diff between markers.

```shell
python -B .agents/manage.py workflow managed-section-diff --target <file> --replacement <file> --format md
```

Use branch policy as an explicit validator before commit or PR handoff:

```shell
python -B .agents/manage.py workflow branch-policy --format json
python -B .agents/manage.py workflow branch-policy --branch feature/example --format json
```

It reports the current or explicitly supplied branch and regex result only; it does not create branches, commit, push, or install anything. Use `--branch` in detached Codex worktrees where Git cannot report a branch name.
