---
name: dotnet-quality-gates
description: Use when running or preparing .NET quality gates for workflow work, including line ending checks, static analysis wrappers, coverage summaries, test-result and BenchmarkDotNet parsing, snapshot artifact checks, anti-slop scanning, and local validation orchestration.
---

# Dotnet Quality Gates

## Goal

Run repeatable .NET quality evidence without hardcoding project-specific commands into workflows.

## Workflow

1. Check changed trees with `validate_line_endings.py`; use `--fix` only when the workflow records the edit.
2. Prefer documented project-native restore/build/test/analyzer commands; use `verify_static_analysis.py` only when no better analyzer exists.
3. Parse existing Cobertura/OpenCover, TRX/JUnit, Stryker-style mutation JSON, and BenchmarkDotNet `*-report-full.json`; compare benchmarks only to explicit baselines and do not run mutation tests by default.
4. Use `--run-snapshot-check` for unapproved `*.received.*` snapshot artifacts and optional Verify `.gitignore` checks.
5. Use `--run-slop-scan` for shortcut patterns across tests, async/EF `CancellationToken`, HTTP resilience, Semantic Kernel, caching, gRPC, source generation, package/API metadata, logging/options, Minimal APIs, ASP.NET ordering, background services, DI, locks/concurrency, LINQ, suppressions, empty catches, delays, warning weakening, and central-package bypasses.
6. Use `$playwright-integration` for browser-test readiness; this skill may reference its report but does not own setup.
7. Use `validate_local_quality.py` as the single workflow gate. Pass explicit targets/evidence paths, or `--packet-root <validation-folder>` to write `local-quality.json` and `local-quality.md`.

## Read-Only Dogfood

Strict read-only/offline runs may inspect routing, skill files, manifests, existing test/coverage/benchmark artifacts, and helper `--help` output. Prefer `module.json.strict_read_only_commands` when a cold agent needs a known-safe command list.

| Command | Strict-safe use | Skip in strict mode |
|---|---|---|
| `validate_line_endings.py` | Target a known folder or file without `--fix`; report both checked and skipped files. | `--fix` or overclaiming that skipped suffixes were checked. |
| `validate_coverage.py` | Parse existing coverage with `--input ... --format json`, or discover .NET targets with `--project-root ... --list-projects-only --format json`. | `--output-json`, `--output-md`, or `--output-generic-xml`. |
| `verify_static_analysis.py` | Use `--plan-only` without `--output-json`; planned `dotnet` commands are documentation only. | Running planned restore/build/test/format commands. |
| `validate_local_quality.py` | Skip unless writes are approved. | Strict dogfood, because it requires `--output-json` or `--packet-root`. |

Skip self-tests/eval-skill when no temp writes are allowed because they create temporary fixtures and output files. Skip restore/build/test/format, analyzers that invoke `dotnet`, `--fix`, `--run-security` output files, local AI, workflow lifecycle commands, sync without `--check`, and generated routing/adapters unless writes and network are approved. These commands are write-capable because they may create reports, package/build/test artifacts, formatted source, security JSON, local AI cache updates, or workflow evidence.

```shell
python -B .agents/skills/dotnet-quality-gates/scripts/validate_line_endings.py <target>
python -B .agents/skills/dotnet-quality-gates/scripts/validate_coverage.py --input coverage.cobertura.xml --format json
python -B .agents/skills/dotnet-quality-gates/scripts/verify_static_analysis.py --project-root <project-root> --plan-only
```

Write-approved examples, not strict dogfood:

```shell
python -B .agents/skills/dotnet-quality-gates/scripts/validate_coverage.py --input coverage.cobertura.xml --output-json validation/coverage-summary.json --output-md validation/coverage-summary.md
python -B .agents/skills/dotnet-quality-gates/scripts/validate_local_quality.py --target <project-root> --test-result TestResults/run.trx --mutation-result stryker-report.json --benchmark-result BenchmarkDotNet.Artifacts/results --run-snapshot-check --run-slop-scan --output-json validation/local-quality.json
python -B .agents/skills/dotnet-quality-gates/scripts/validate_local_quality.py --target <project-root> --run-security --docs-target docs --packet-root validation
```

For console-only replay, omit `--output-*` from parsers and use `--format json` where available. Run restore/build/test only when documented, prerequisites exist, and fresh evidence is needed. Parse existing TRX/JUnit/Cobertura/OpenCover artifacts for replayed runs, CI handoffs, or missing SDK/tool prerequisites. Local AI may summarize the report only when local AI use is approved:

```shell
python -B .agents/manage.py local-ai task --task validation-triage --input validation/local-quality.md
```

Fallback without local AI: read `summary`, failed `checks`, and `skipped` in `local-quality.json`.

## Extension Points

Workflow wrappers such as `story-local-quality-profile` and `bug-regression-quality-profile` pass explicit target, coverage, test-result, docs, security, and output paths to `validate_local_quality.py`; do not rely on the current directory. Evidence stays in the workflow validation folder and uses normalized per-check fields: `name`, `kind`, `ok`, `status`, `duration_seconds`, `summary`, `evidence_paths`, `format`.

The skill manifest keeps `outputs` empty because reports are caller-owned workflow or project evidence, not durable skill-owned artifacts.

## Rules

- Prefer the target repo's own build and test commands when they exist.
- Do not install tools automatically. Report missing `dotnet`; use `$playwright-integration` for `node`, `npm`, or Playwright readiness.
- Treat `verify_static_analysis.py` without `--plan-only` as a build/format/test runner, not a read-only parser.
- The manifest network flag covers the skill helpers themselves; project-native restore/build/test commands may still contact package feeds or services.
- Optional setup checks are non-blocking unless the workflow made them required; report skipped, failed, and continue decisions.
- Do not publish coverage, scanner, or benchmark results from this skill.
- Keep validation reports under the workflow folder or target project's normal test output folder.
- Record skipped gates with the reason.
- Prefer script-level parallelism inside `validate_local_quality.py` over spawning agents for analyzer execution; only run checks concurrently when inputs are stable, work is independent, and there are no shared write targets.
- Side or sub agents may interpret failed or ambiguous evidence only as read-only reviewers.

## Validation

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/dotnet-quality-gates
python -B .agents/skills/dotnet-quality-gates/scripts/run_self_tests.py
```

Run the self-test for skill implementation validation only when temporary fixture writes are allowed; it is not part of strict no-temp dogfood.

## Stop Rules

- Stop before running if a wrapper is missing from `module.json`, paths are missing, or output would escape the evidence folder.
- Stop if a wrapper command would run outside the requested target path.
- Stop before using `--fix` if unrelated dirty files would be modified.
- Stop before claiming coverage evidence if no coverage input was parsed.

## Completion Contract

Report commands, target paths, evidence schema version, reports, validation/pass-fail status, skipped gates, blocked prerequisites, failed-command summaries, and remaining validation risk.

Report `Skill used: dotnet-quality-gates - <reason>` when this skill materially affected the work.
