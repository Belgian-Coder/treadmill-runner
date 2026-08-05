# Mermaid Style Guide

Use for Azure DevOps Markdown/wiki Mermaid diagrams that should also render on GitHub.

## Portable Publishing Pattern

For durable repo docs:

1. Keep editable Mermaid in sibling `diagrams/*.mmd`.
2. Render a nearby dark transparent `.svg`.
3. Embed and link the SVG.
4. Add a nearby `.mmd` source link.

```markdown
[![Workflow diagram](diagrams/workflow-process-diagram.svg)](diagrams/workflow-process-diagram.svg)

Source: [Mermaid](diagrams/workflow-process-diagram.mmd)
```

Linked SVGs stay scalable in narrow Markdown columns. Render with Mermaid CLI dark theme, transparent background, intrinsic dimensions, and vertical padding so small diagrams do not inflate.

Convert Azure Mermaid blocks with:

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/materialize_diagrams.py <path>
```

## Wrappers

| Target | Wrapper |
|---|---|
| Durable Azure DevOps and GitHub docs | Linked SVG from adjacent `.mmd` source |
| Azure DevOps canonical | `::: mermaid` with four-space body and closing `:::` |
| Azure DevOps accepted | `:::mermaid` with four-space body and closing `:::` |
| GitHub native Mermaid | Fenced `mermaid` only when Azure compatibility is not required |

For VS Code preview, use `Markdown Preview Mermaid Support` (`bierner.markdown-mermaid`). Azure DevOps Wiki renders both colon spellings; prefer the spaced form for temporary blocks. Repo validation rejects Markdown Mermaid blocks by default; use `--allow-markdown-blocks` only before materializing.

Run the preview setup check when the task involves VS Code preview behavior:

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/setup_vscode_mermaid_preview.py --auto-install --format markdown --non-blocking
```

If VS Code preview flashes then hides diagrams, suspect extension conflict. The setup script reports `mermaidchart.vscode-mermaid-chart` and similar extensions as evidence only and never changes workspace settings. Visual Studio/Rider are not VS Code; continue with static/render validation.

## Azure-Safe Body Rules

- Choose the type with `diagram-selection-guide.md`; use `erDiagram` for tables/schemas and `graph TD;`/`graph LR;` for process, workflow, setup, architecture.
- Use stable ASCII node ids, quoted labels such as `start["Start"]`, semicolon-terminated graph statements, and semantic shapes: `(["Start"])`, `["Process"]`, `{"Decision"}`, `[("Storage")]`.
- Label decision branches and cross-boundary edges.
- Split graph diagrams above roughly 12 nodes or 18 edges.

Avoid `flowchart`, init/theme directives, HTML labels, Font Awesome icons, long arrows like `---->`, click callbacks, `classDef`, `style`, `linkStyle`, and links to/from subgraph ids.

## Label Hygiene

- Keep labels short; quote or simplify labels with punctuation, brackets, slashes, pipes, hashes, ampersands, angle brackets, or quotes.
- Reword labels ending with `/`.
- Avoid lowercase `end` in graph labels; use `End` or reword.

## Validation

After editing Markdown Mermaid blocks, materialize, then run render-capable validation on changed Markdown and generated `.mmd` paths. Use `--non-blocking` inside workflows so setup/diagram issues become evidence, not blockers:

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/materialize_diagrams.py <path>
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py <path> --render --require-render --format markdown --non-blocking
```

With `--non-blocking`, exit code 0 only means the report was emitted. Read the output status/errors/render section before treating validation as passed.

Draft-only syntax check before materialization:

```shell
python -B .agents/skills/mermaid-diagrams-azure-devops/scripts/validate_mermaid.py <path> --allow-markdown-blocks --format markdown --non-blocking
```

The validator uses local `mmdc` when present. If missing, it checks compatible Node.js/npm and may set up Mermaid CLI when possible. Use static-only for quick inspection or `--no-auto-install-mmdc` when setup attempts are forbidden.
