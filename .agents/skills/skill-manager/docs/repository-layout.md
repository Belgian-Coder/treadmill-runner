# Repository Layout

This repo keeps canonical skill/workflow sources and generated adapters for tools that need different discovery paths. Use routing files for low-context selection; registries are machine metadata, not normal reading context.

## Canonical Paths

| Path | Rule |
|---|---|
| `.agents/skills/<skill-name>/` | Canonical skill folders. Edit by hand. |
| `.agents/routing.md`, `.agents/registry.json` | Generated skill routing/registry. The JSON registry is tool-only machine metadata; agents must not load or edit it by hand. |
| `automations/<workflow-name>/` | Workflow modules with `WORKFLOW.md`, `module.json`, optional `instructions.md`, and declared outputs. |
| `automations/routing.md`, `automations/registry.json` | Generated workflow routing/registry. The JSON registry is tool-only machine metadata; agents must not load or edit it by hand. |
| `.claude/CLAUDE.md`, `.claude/skills/<skill>/SKILL.md` | Generated Claude adapters. Do not edit by hand. |
| `.github/copilot-instructions.md`, `GEMINI.md`, `.continue/rules/repository-instructions.md`, `.aider.conf.yml` | Generated from `AGENTS.md`. Do not edit by hand. |
| `.agents/skills/*/scripts/` | Owning command implementations and helpers. |

## Owners

| Concern | Owner |
|---|---|
| Skill lifecycle, intake, review, routing, adapters | `$skill-manager` |
| Workflow lifecycle, validation, hooks, run evidence | `$workflow-manager` |
| Azure DevOps-compatible Mermaid | `$mermaid-diagrams-azure-devops` |
| Local model setup, brokered tools, embedding benchmarks, vision | `$local-ai-helper` |
| Benchmark runs, token estimates, comparisons | `$agent-benchmarking` |

## Generated Files

After active skill or workflow changes:

```shell
python -B .agents/manage.py sync
python -B .agents/manage.py check
```

Validation fails when project instructions, routing, registries, or adapters are stale.

## Placement Rules

- Put skill lifecycle decisions in `skill-manager/docs/`.
- Put workflow lifecycle decisions in `workflow-manager/docs/`.
- Keep workflow modules and generated workflow routing under `automations/`.
- Keep root docs focused on purpose, layout, daily commands, and practical usage.
- Use Markdown for human-authored files unless JSON materially improves validation or interoperability.
- Keep command implementations with the skill or workflow that owns them.
- Do not add committed IDE settings, personal trust entries, shell wrappers, batch files, or PowerShell wrappers in active paths.

## Portability

Copying `.agents/`, `.claude/`, `.github/`, `.continue/`, `automations/`, `AGENTS.md`, `GEMINI.md`, and `.aider.conf.yml` into another project should preserve usable skills and workflows. `.agents/skills` remains canonical; generated adapters expose canonical behavior to specific tools.

## Mermaid

Use `$mermaid-diagrams-azure-devops` for Azure DevOps Mermaid authoring, static validation, and optional local render checks. Durable docs use adjacent `.mmd` sources plus linked `.svg` renders. Use `::: mermaid` only for drafts before materializing diagrams.

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py README.md .agents/skills/skill-manager/docs .agents/skills/workflow-manager/docs --non-blocking
```

## Cache Noise

Maintained commands must disable bytecode generation with `python -B`, `PYTHONDONTWRITEBYTECODE=1`, or `sys.dont_write_bytecode = True` before importing repo-local modules. `__pycache__` under active paths is a validation error because it adds noise and is not portable project surface.
