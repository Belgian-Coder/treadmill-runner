---
title: Documentation Map
type: index
status: active
owner: skill-manager
audience: both
updated: 2026-08-02
---

# Documentation Map

Every root documentation file under `docs/**/*.md` starts with frontmatter. This map is a navigation aid built from those docs; it does not replace per-file metadata.

When adding a root doc:

1. Start the file with the default frontmatter from [Documentation Metadata](doc-metadata.md).
2. Pick `owner`, `type`, `status`, `audience`, and `updated` before writing the body.
3. Link the file from this map or [Start Here](../start-here.md).
4. Run `python -B .agents/manage.py check-repo-health --summary --compact --json`.

## Harness Docs

| Doc | Owner | Type | Audience | Purpose |
|---|---|---|---|---|
| [Copy Into A Project](../harness/copy-into-project.md) | skill-manager | guide | both | Copy the harness into a target project without run history or local state. |
| [Initialize Current Project](../harness/initialize-current-project.md) | skill-manager | guide | both | Run setup-driven navigation map and project-context initialization in a target project. |
| [No Python Or No Admin](../harness/no-python.md) | skill-manager | guide | both | Explain the no-admin prerequisite path when Python is missing. |
| [Reusable AI Harness](../harness/reusable-ai-harness.md) | skill-manager | guide | both | Explain the reusable harness shape and usage model. |
| [Setup](../harness/setup.md) | skill-manager | runbook | both | First-run and setup validation path. |
| [Why Use This Harness](../harness/why-use-this-harness.md) | skill-manager | guide | both | Explain benefits, tradeoffs, and the portable tool security model. |

## Operations Docs

| Doc | Owner | Type | Audience | Purpose |
|---|---|---|---|---|
| [Agent Start](../agent-start.md) | skill-manager | guide | agent | Compact first-read path for agents in copied projects. |
| [After Failure](../operations/after-failure.md) | skill-manager | runbook | agent | Recover after validation or workflow failures. |
| [Daily Agent Path](../operations/daily-agent-path.md) | skill-manager | guide | agent | Normal route-first daily agent workflow. |
| [Daily Use](../operations/daily-use.md) | skill-manager | guide | both | Common daily commands and usage paths. |
| [Repository Search](../operations/repository-search.md) | skill-manager | guide | agent | Direct repository search, measured index-removal decision, and reintroduction gate. |
| [Token Savings](../operations/token-savings.md) | local-ai-helper | guide | both | Local-first token-saving controls, warm-server guidance, and next improvements. |
| [Validation Evidence And Parallelism](../operations/validation-evidence-and-parallelism.md) | skill-manager | guide | agent | Evidence-first validation and safe parallel command usage. |

## Workflow Docs

| Doc | Owner | Type | Audience | Purpose |
|---|---|---|---|---|
| [Quality Evidence Packets](../workflow/quality-evidence-packets.md) | workflow-manager | reference | agent | Evidence packet expectations for workflow runs. |
| [Spec Driven Workflow Gates](../workflow/spec-driven-gates.md) | workflow-manager | reference | agent | Clarification, requirements quality, coverage, complexity, layering, story slicing, and bug-stage gates for workflow plans. |
| [Using Workflows](../workflow/using-workflows.md) | workflow-manager | guide | both | Human-facing workflow prompts, commands, files, and tradeoffs. |
| [Workflow Quickstart](../workflow/workflow-quickstart.md) | workflow-manager | guide | agent | Quick workflow start/resume path. |
| [Workflows](../workflow/workflows.md) | workflow-manager | guide | agent | Workflow lifecycle, diagrams, context, hooks, and validation. |

## Reference Docs

| Doc | Owner | Type | Audience | Purpose |
|---|---|---|---|---|
| [Actionable Backlog](actionable-backlog.md) | skill-manager | reference | agent | Follow-up improvement backlog. |
| [Commands](commands.md) | skill-manager | reference | both | Repository command index. |
| [Customizing The Harness](customization-guide.md) | skill-manager | guide | both | Canonical edit locations, generated-file boundaries, and cross-host customization examples. |
| [Delegation And Parallel Safety](delegation-and-parallel-safety.md) | workflow-manager | reference | agent | Evidence-gated delegation, thread telemetry, isolation modes, and serial fallback. |
| [Documentation Map](documentation-map.md) | skill-manager | index | both | Human-readable map of root docs. |
| [Documentation Metadata](doc-metadata.md) | skill-manager | reference | agent | Frontmatter contract and defaults. |
| [Documents](documents.md) | document-artifacts | reference | both | Document artifact handling guidance. |
| [Evidence Retention](evidence-retention.md) | skill-manager | policy | both | Tracked evidence, dogfood run, fixture, and cleanup defaults. |
| [Import Review Summary](import-review-summary.md) | skill-manager | reference | agent | Durable outcomes from reviewed temporary import evidence. |
| [Maintenance Policy](maintenance-policy.md) | skill-manager | policy | agent | Maintenance and ownership rules. |
| [Project Policy Configuration](../harness/policy-configuration.md) | skill-manager | guide | both | Discover and configure project limits, warning actions, command budgets, and local-AI cost policy. |
| [Model Compatibility And Routing](model-compatibility-and-routing.md) | workflow-manager | reference | agent | Semantic worker profiles, declared model targets, prompt overlays, host compatibility, evidence, and promotion boundaries. |
| [Task And Model Orchestration](../../orchestration.md) | workflow-manager | reference | agent | Project task responsibilities, dynamic delegation, ordered per-host model preferences, and active-model fallback. |
| [Project Context Generator Dogfood](project-context-generator-dogfood.md) | project-context-generator | reference | agent | Dogfood evidence for generated project context detection and validation command discovery. |
| [Release](release.md) | skill-manager | runbook | agent | Release and finish validation path. |
| [Tools And Search Options](tools-and-search.md) | skill-manager | reference | both | Compare deterministic tools, routing, repository search, workflow context, feedback, and host-provided search/connectors. |

## Project Docs

| Doc | Owner | Type | Audience | Purpose |
|---|---|---|---|---|
| [Project Documentation](../project/README.md) | project-context-generator | index | both | Project docs entry point and context-generation guidance. |
| [Project Context](../project/project-context.md) | project-context-generator | project-context | both | Reviewed baseline technologies, commands, structure, validation proof paths, and boundaries for workflow work. |
