# Azure DevOps Mermaid Limits

Source: Microsoft Learn Azure DevOps Markdown guidance, checked 2026-05-02: <https://learn.microsoft.com/en-us/azure/devops/project/wiki/markdown-guidance?view=azure-devops&preserve-view=true#work-with-mermaid-diagrams>.

Refresh this source check before changing supported type rules or making release-critical Azure DevOps wiki compatibility claims; Azure DevOps support can drift behind Mermaid upstream.

Azure DevOps Wiki renders Mermaid with colon blocks: inline opener `::: mermaid`, indented body, closing `:::`. Keep live examples materialized before commit.

The compact opener `:::mermaid` is accepted only for pre-materialization draft checks with `--allow-markdown-blocks`. Durable repo docs must use linked SVG embeds generated from adjacent `.mmd` files.

Fenced Mermaid wrappers are out of scope for this Azure DevOps skill.

## Supported Types

Microsoft documents: `sequenceDiagram`, `gantt`, `graph` not `flowchart`, `classDiagram`, `stateDiagram`, `stateDiagram-v2`, `journey`, `pie`, `requirementDiagram`, `gitGraph`, `erDiagram`, and `timeline`.

The validator accepts this supported set and applies stricter repo style to graph diagrams.

## Validation Rules

Repo validation rules:

- Use `graph`, not `flowchart`.
- Avoid most HTML tags.
- Avoid Font Awesome syntax.
- Avoid LongArrow syntax like `---->`.
- Avoid custom init/theme directives and custom styling.
- Avoid click callbacks and other interactive behavior.
- Avoid direct links to or from subgraph ids; link actual nodes inside the subgraph instead.
- Keep bodies indented inside Azure Mermaid colon blocks.

Azure DevOps support can lag Mermaid upstream. Static validation plus optional `mmdc` render validation catches common failures, but Azure DevOps preview remains the final compatibility check for release-critical wiki pages.
