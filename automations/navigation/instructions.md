# Navigation Instructions

## Always Load

- `module.json`
- `WORKFLOW.md`
- `artifacts/maps/HANDOFF.md` when present
- `artifacts/maps/NAVIGATION.md` when present

Strict read-only/offline/no-profile/no-temp/no-write dogfood does not write generated maps, project-context drafts, lifecycle evidence, context evidence, run state, raw JSON maps, caches, profiles, or temporary fixtures; report skipped write steps instead.

## Phase: scan

- [ ] Read: `WORKFLOW.md`, `module.json`, and project guidance files.
  Do: collect deterministic file, manifest, command, symbol, and import facts.
  Write: no files in check mode; generated map payloads in write mode.
  Done when: scan facts are available.
  If blocked: report unreadable roots or files.

## Phase: project-context

- [ ] Read: compact navigation Markdown and `docs/project/project-context.md` when present; keep raw navigation JSON such as `handoff.json` and `staleness.json` inside deterministic commands.
  Do: check that project purpose, technologies, run commands, validation commands, folder structure, generated files, external-service boundaries, and Mermaid diagrams are confirmed.
  Write: `docs/project/project-context.md` when missing, or `artifacts/maps/PROJECT_CONTEXT_DRAFT.md` when an existing context must not be overwritten.
  Done when: the project context is reviewed or unresolved facts are explicit.
  If blocked: stop implementation work and record the missing project facts.

## Phase: write

- [ ] Read: scan output.
  Do: write map outputs under `artifacts/maps/`.
  Write: `NAVIGATION.md`, `HANDOFF.md`, `TECHNICAL_CONTEXT.md`, `CONVENTIONS.md`, plus tool-only raw JSON indexes `handoff.json` and `staleness.json`.
  Done when: all declared outputs are present.
  If blocked: keep the failing path and command output.

## Phase: check

- [ ] Read: check command status; compare committed raw navigation JSON and freshly generated outputs inside the tool.
  Do: compare expected outputs with committed files.
  Write: status only unless `--write` is provided.
  Done when: stale outputs and source changes are explicit.
  If blocked: report the failing path and stop before trusting stale maps.

## Stop Rules

- Stop before reading suspected sensitive local values.
- Stop before claiming maps are fresh when `check` reports stale outputs.

## Completion Contract

Report target root, mode, generated or checked map paths, project-context status, stale source changes, skipped files, failed commands, and remaining navigation risk.
