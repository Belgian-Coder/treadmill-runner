# Documentation

Use this file when .NET changes require project, API, architecture, or repository docs.

## Project Docs

- Preserve existing documentation style and tooling before suggesting new tools.
- README updates should include changed behavior, SDK/workload/runtime, local commands, configuration prerequisites, and validation evidence when known.
- CONTRIBUTING, issue templates, and PR templates stay repository-wide unless the repo already keeps per-component docs.
- Do not add badges, generated changelogs, release notes, or publish instructions unless the repo owns those signals.

## Public API Docs

- For libraries, check whether XML documentation is already enabled before adding or changing public API comments.
- XML docs explain behavior, parameters, return values, exceptions, cancellation, thread safety, and examples only where they reduce ambiguity.
- Prefer `<inheritdoc/>` when the inherited contract is accurate.
- Do not enable warning-as-error policies for missing XML docs unless the project already enforces them.

## Architecture Diagrams

- Mermaid diagrams should reflect actual project references, runtime boundaries, data stores, queues, external services, and deployment targets.
- Keep diagrams reviewable in Markdown; split architecture, sequence, deployment, and domain diagrams when dense.
- Use `mermaid-diagrams-azure-devops` when diagrams must render inside Azure DevOps Markdown or wiki pages.
- Treat generated diagrams as drafts until a project owner confirms boundaries.

## Tooling Choices

- DocFX fits existing .NET libraries with XML documentation investment.
- Starlight or Docusaurus fit teams already maintaining a docs site and JavaScript toolchain.
- GitHub-native Markdown fits small repos, internal tools, and teams optimizing for low setup cost.
- OpenAPI, changelog generation, and documentation site deployment belong to their owning API or delivery workflow; link to outputs rather than re-creating them.
