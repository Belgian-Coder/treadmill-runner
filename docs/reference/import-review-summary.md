---
title: Import Review Summary
type: reference
status: active
owner: skill-manager
audience: agent
updated: 2026-06-03
---

# Import Review Summary

Temporary import evidence from external harness examples was reviewed and reduced into active repo behavior. The raw `evidence/import-temp-artisan-assets/` files were removed so the repository no longer keeps completed candidate-review scratch material.

## Durable Outcomes

- Candidate ideas must be rewritten into accepted skill or workflow owners instead of copied wholesale.
- Workflow runs, context packets, and dogfood output stay local by default.
- Useful benchmark examples belong in small suite fixtures, not historical run folders.
- Install and copy procedures must exclude run history, local AI state, caches, secrets, model payloads, and Git state.

## Future Imports

Use `candidate-import-workflow` for new imports. Keep raw candidate files in temporary state while reviewing them, then promote only durable behavior into docs, suites, scripts, templates, or small fixtures.

## 2026-06-03 Temp Review

The ignored `temp/` tree was re-reviewed through `temp/SUMMARY.md`, targeted inventory commands, and `analyze-location temp --format json`.

Decision: delete the raw temp tree after capturing reusable lessons. It contained thousands of duplicated candidate skills, source mirrors, plugin bundles, generated assets, and many shell scripts that violate this repo's active-path policy. No folder is safe to import wholesale.

Useful lessons reduced into active work:

- Workflow creation now scaffolds diagrams and a `suites/workflow-evals.json` file so fresh agents do not have to infer scorecard requirements.
- New workflow docs now call out copyable prompts, `Read/Do/Write/Done when/If blocked` phase steps, context commands, focused evals, and repo-wide sync behavior.
- The existing `repo-navigation`, `.NET`, document, Mermaid, local-AI, benchmarking, and workflow skills already cover the most useful candidate themes from `context-pack-main`, `dotnet-artisan-main`, `Test-flow`, and the broader plugin catalogs.

Rejected for direct import:

- Large candidate mirrors with mixed licensing, generated files, platform-specific hooks, and local service assumptions.
- Shell, batch, PowerShell, and command-wrapper scripts that would require Python rewrites before active use.
- MCP/plugin/service wiring that needs explicit product decisions and credential boundaries before reuse.

Future import work should start from a specific need, not from the deleted scratch tree. Re-fetch a narrow upstream candidate only when an owner, validation plan, and rewrite target are already known.
