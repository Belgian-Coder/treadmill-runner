---
name: mermaid-diagrams-azure-devops
description: Use when creating, editing, normalizing, or validating Azure DevOps-compatible Mermaid diagrams in Markdown or wiki documentation, including Azure wrapper checks and local render checks with Mermaid CLI when available.
---

# Mermaid Diagrams Azure DevOps

## Goal

Create and validate Azure DevOps-compatible Mermaid diagrams. Durable Markdown uses linked SVG embeds from adjacent `.mmd` source; Mermaid blocks are draft-only and require `--allow-markdown-blocks`.

## Workflow

1. For read-only/offline dogfood with strict no-temp/no-user-profile boundaries, use docs, help, `validate_skill.py`, `inspect-skill --fast`, `validate_mermaid.py <path> --static-only --format markdown`, `validate_mermaid.py <path> --inventory --format markdown`, and `materialize_diagrams.py <path> --dry-run --no-auto-install-mmdc`. Use `--doctor` only when temp render probing and VS Code profile inspection are allowed or source-reviewed; it makes no repo writes or installs, but may use temporary files when `mmdc` exists and may inspect user-level VS Code extension state.
2. For VS Code preview when user-level extension installs are allowed, run setup. It installs only the recommended extension when VS Code CLI works, reports conflicts, never changes workspace settings, and skips Visual Studio/Rider-only environments. Without `--auto-install`, setup is no-install inspection, but it still queries user-level VS Code extension state; skip it when profile inspection is forbidden.

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/setup_vscode_mermaid_preview.py --auto-install --format markdown --non-blocking
```

3. Read `.agents/skills/mermaid-diagrams-azure-devops/docs/diagram-selection-guide.md`; use style/Azure limit docs for non-trivial diagrams.
4. Pick an Azure-supported type: `erDiagram`, `graph TD;`/`LR;`, `sequenceDiagram`, `stateDiagram-v2`, `classDiagram`, `gantt`, `pie`, `journey`, `requirementDiagram`, `gitGraph`, or `timeline`.
5. Use Azure-safe syntax: simple arrows, quoted labels, ASCII ids, semicolon-terminated graph statements. Repo-authored Azure-compatible docs must avoid `flowchart`, init/theme blocks, HTML labels, Font Awesome, LongArrow `---->`, click callbacks, custom styling, subgraph-id links, and platform-specific features. Exceptions are outside this skill or require a documented waiver before validation rules are relaxed.
6. For durable Markdown, keep `.mmd` and SVG in nearby `diagrams/`, then embed SVG plus source link.

```text
[![Example diagram](diagrams/page-example.svg)](diagrams/page-example.svg)

Source: [Mermaid](diagrams/page-example.mmd)
```

7. Convert Azure blocks into portable SVG embeds and `.mmd` sources:

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/materialize_diagrams.py <path>
```

8. Use Azure wrappers only for short-lived drafts before materialization; prefer the spaced wrapper and validate drafts with `--allow-markdown-blocks`.

```text
::: mermaid
    graph TD;
      A["Start"] --> B["Done"];
:::
```

9. After diagram edits, run render-capable validation by default. Include changed Markdown plus generated `diagrams/` sources when present. Strict no-temp dogfood overrides this default: use static/inventory/dry-run evidence and report render skipped because rendering can invoke `mmdc` and temporary files.

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py <path> --render --require-render --format markdown --non-blocking
```

With `--non-blocking`, exit code 0 is not validation evidence; parse the output `valid`/`ok` status, errors, warnings, and render section before reporting success.

10. Use static-only/no-auto-install modes only when requested or for quick inspection. Use `--allow-markdown-blocks` only before materialization:

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py <path> --allow-markdown-blocks --format markdown --non-blocking
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py --changed-only --static-only --format markdown
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py docs --fix --static-only
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py docs --inventory --format markdown
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py <path> --doctor --format json --non-blocking
```

Use `local-ai-helper` for rendered-PDF inspection only after deterministic validation/rendering evidence. Fallback without local AI: use static warnings, render errors, and inventory output.

## Rules

- `--render` uses `mmdc`; `--render --require-render` may set up Mermaid CLI when compatible Node/npm exist and fails if setup/rendering fails.
- `materialize_diagrams.py` writes durable `.mmd` plus dark transparent SVG files with intrinsic dimensions/padding, replacing blocks with linked SVG embeds/source links.
- `materialize_diagrams.py` refuses to overwrite existing generated target paths during new materialization; run `--dry-run` first when a folder already has `diagrams/` assets, and use `--refresh-existing` only for linked source/SVG pairs.
- Strict read-only/offline excludes `setup_vscode_mermaid_preview.py --auto-install`, `validate_mermaid.py --fix`, `validate_mermaid.py --render` without `--no-auto-install-mmdc`, required rendering that can invoke `mmdc`, and materialization without `--dry-run`.
- `--render --no-auto-install-mmdc` disables setup only; it can still invoke an existing `mmdc` and create temporary render inputs/outputs, so skip it under strict no-temp.
- Prefer SVG over PNG; GitHub and Azure DevOps render Markdown images, and readers can open SVGs to zoom.
- `--non-blocking` preserves errors with exit code 0; parse output status instead of trusting the shell exit code. `--no-auto-install-mmdc` disables setup; `--auto-install-mmdc` is explicit/non-interactive.
- `--static-only` never renders; `--changed-only` scans changed Markdown; `--inventory` lists diagrams; `--fix` applies mechanical repairs.
- `--doctor` is repo-read-only/no-install: no `--fix`, Mermaid CLI install, or VS Code extension install. It is not strict no-temp/no-profile unless source-reviewed in the current environment.
- VS Code setup is best-effort and never uninstalls, disables extensions, or writes workspace settings.
- Do not ask whether `mmdc`, Node.js, or npm is installed; scripts detect those facts.
- Do not skip render-capable validation after diagram edits unless static-only/no setup was requested.
- Keep labels short and split oversized graphs. Generated SVGs keep intrinsic width so small diagrams do not fill the page; large diagrams should link to the SVG target.

## Validation

Use materialize/validate steps above. `--doctor` is the read-only setup and validation packet.

## Completion Contract

Report Markdown paths inspected/changed, generated `.mmd`/`.svg` assets, diagram types, setup status, validation command/result, render status, non-blocking failures, skipped/blocked checks, Azure DevOps/GitHub risks, and `Skill used: mermaid-diagrams-azure-devops - <reason>` when material.

## Stop Rules

- For workflow steps, do not block larger progress on Mermaid CLI or VS Code preview setup; report and continue.
- For dedicated diagram tasks, stop before finalizing if static validation reports unsupported syntax, non-Azure wrappers, or failed required rendering.
- Do not add tool settings, wrapper scripts, or remote rendering services.
