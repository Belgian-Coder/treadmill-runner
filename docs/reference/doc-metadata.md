---
title: Documentation Metadata
type: reference
status: active
owner: skill-manager
audience: agent
updated: 2026-06-10
---

# Documentation Metadata

Root documentation under `docs/**/*.md` starts with a small frontmatter block by default so agents can route, audit, and refresh documentation without reading every file first.

## Required Fields

```yaml
---
title: Documentation Metadata
type: reference
status: active
owner: skill-manager
audience: agent
updated: 2026-05-27
---
```

Use this block for every new root documentation file. Pick values before writing the body; do not add a root doc first and backfill metadata later.

- `title`: human-readable document title.
- `type`: one of `guide`, `reference`, `policy`, `runbook`, `project-context`, `index`, or `adr`.
- `status`: one of `active`, `draft`, `generated`, `reviewed`, or `archived`.
- `owner`: lowercase owning skill, workflow, or `repo` id.
- `audience`: one of `human`, `agent`, or `both`.
- `updated`: last intentional content review date in `YYYY-MM-DD` format.
- `applies_to`: optional simple text for a specific module, project, or workflow boundary.

## Rules

- Keep the block short; do not duplicate `module.json`, `run.json`, or evidence packet facts.
- Update `updated` when the document content changes materially.
- Link new docs from `docs/start-here.md` or [Documentation Map](documentation-map.md) so reachability checks can find them.
- Treat frontmatter as the source of truth for per-document metadata; the documentation map is a navigation index, not a replacement for frontmatter.
- Use `status: generated` for setup-generated `docs/project/project-context.md` before human review.
- Use `status: reviewed` for `docs/project/project-context.md` after the project baseline has been checked.
- Keep workflow run state in run packets, not in documentation frontmatter.

Validation is enforced by:

```shell
python -B .agents/manage.py check-repo-health --summary --compact --json
python -B .agents/manage.py check
```
