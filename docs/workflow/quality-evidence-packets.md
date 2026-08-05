---
title: Quality Evidence Packets
type: reference
status: active
owner: workflow-manager
audience: agent
updated: 2026-05-27
---

# Quality Evidence Packets

Shared evidence should stay parseable across .NET, SonarQube, security, and Playwright workflows.

## Common Fields

Use `schema_version`, `tool`, `ok`, `status`, `summary`, `checks[]`, `skipped[]`, optional `failures[]`, and optional `artifacts[]`.

`checks[]` carries deterministic check names, results, summaries, and evidence paths.

## Ownership

- `dotnet-quality-gates`: tests, coverage, line endings, static analysis, SARIF ingestion.
- `sonarqube-diagnostics`: read-only SonarQube exports, issue facts, coverage, quality gates.
- `dotnet-security-review`: local pattern scans, suppressions, generated/binary skips, SARIF.
- `playwright-integration`: browser readiness, spec lint, result parsing, flake diagnosis.

Workflows may orchestrate packets. Skills should not import each other only to emit the common shape.

Local AI may summarize declared tasks such as `validation-triage` or `code-review`; deterministic packets stay authoritative.
