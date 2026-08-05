---
title: Actionable Backlog
type: reference
status: active
owner: skill-manager
audience: both
updated: 2026-07-25
---

# Actionable Backlog

Short queue for measurable repository improvements. Canonical behavior still belongs in `module.json`, entry files, suites, scripts, or docs.

## Criteria

- Prefer work that reduces unsupported claims, stale generated output, manual evidence, duplicate instructions, or context load.
- Add or update deterministic evidence before prose-only guidance.
- Keep local AI optional, cache-aware, read-only by default, and subordinate to deterministic checks.
- Use temporary workflow dogfood runs; promote durable proof into fixtures, suites, scripts, templates, docs, or compact reports.

## Queue

| Priority | Owner | Cleanup | Evidence Or Validation | Status |
|---|---|---|---|---|
| P0 | `skill-manager` | Enforce docs frontmatter, docs reachability, and documentation-map coverage. | `check-repo-health`; self-test for unmapped docs. | Done |
| P0 | `workflow-manager` | Replace retained dogfood-run eval proof with lifecycle smoke that cleans up temporary runs. | `workflow eval --all`; workflow-manager self-tests. | Done |
| P0 | `skill-manager` | Support both intentional local-only validation and consumer-owned least-privilege GitHub Actions without conflating their evidence. | `finish`, `dashboard`, and GitHub validation self-tests. | Done |
| P1 | `local-ai-helper` | Add automatic warm-server mode for batches of local text tasks after persistent-server JSON schema handling is validated. | `cost-policy --check`; local text task self-tests; benchmark latency comparison. | Done |
| P1 | `mermaid-diagrams-azure-devops` | Split validator implementation by responsibility and keep public CLI/import surface compatible. | Mermaid self-tests; full static Mermaid validation; repo `check`. | Done |
| P1 | `skill-manager` | Make full materialized Mermaid validation a hard repo-health/check gate with inventory counts. | `check-repo-health`; skill-manager self-tests. | Done |
| P1 | `project-context-generator` | Dogfood project context generation on this repo without overwriting reviewed context; ignore fixture tech signals and discover harness commands. | [Project Context Generator Dogfood](project-context-generator-dogfood.md); project-context-generator self-tests. | Done |
| P1 | `skill-manager` | Add exact next-command suggestions to `cost-policy` based on current diff and context budget state. | `cost-policy --check`; skill-manager self-tests. | Done |
| P1 | `workflow-manager` | Replace broad workflow start fallback bundles with bounded deterministic context evidence while preserving approval and evidence gates. | `workflow context-evidence --event start`; workflow eval suites. | Done |
| P1 | `local-ai-helper` | Remove indexed repository retrieval after paired measurement showed lower quality and higher latency than direct `rg`. | Repository-search utility suite; local-AI, workflow-manager, and skill-manager self-tests. | Done |
| P1 | `external-reference-manager` | Check stale pins and compact cards before relying on external references. | `reference-refresh` smoke/eval and pinned-reference checks. | Done |
| P2 | `dotnet-quality-gates` | Improve coverage/test-result parser fixtures for more real-world layouts. | Parser fixture self-tests and changed-file validation. | Done |
| P2 | `local-ai-helper` | Split remaining large setup helper modules when a focused change touches them. | Script hotspot report and local-ai self-tests. | Done |
