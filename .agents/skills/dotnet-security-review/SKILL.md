---
name: dotnet-security-review
description: Use when reviewing .NET, ASP.NET Core, Blazor, API, worker, or configuration changes for application security risks, or when scanning changed files for .NET-adjacent risky security patterns with deterministic evidence.
---

# Dotnet Security Review

## Goal

Find security risks with bounded .NET review guidance and deterministic changed-file scanner evidence without uploading secrets, claiming broad all-language coverage, or replacing project-owned threat modeling.

## Workflow

1. Define the review scope: changed files, app boundaries, auth/authz flows, input paths, secrets, data stores, network calls, dependencies, deployment config, and write-capable code paths.
2. Use `docs/review-scope.md`, `docs/aspnet-security.md`, and `docs/severity-and-reporting.md` for manual .NET and ASP.NET Core review.
3. Run scanner evidence when local changed-file or selected-path pattern checks are useful. Use the read-only form for console evidence:

```shell
python -B .agents/skills/dotnet-security-review/scripts/dotnet_security_review.py scan --target . --changed-only
```

Use the write-capable evidence form only when caller-owned output files are approved:

```shell
python -B .agents/skills/dotnet-security-review/scripts/dotnet_security_review.py scan --target <files-or-dirs> --output-json evidence/dotnet-security-review.json --output-md evidence/dotnet-security-review.md
```

4. Treat scanner results as evidence, not a complete security review. Confirm exploitability, data sensitivity, auth boundaries, and deployment context before severity claims.
5. Hand implementation fixes to `dotnet-engineering`, `dotnet-legacy`, or project security owners after concrete findings are identified.

## Read-Only Dogfood

For strict read-only/offline review, use routing reads, docs, help, `inspect-skill --fast`, `validate_skill.py`, `module.json.strict_read_only_commands`, inspect eval suites without executing them, and scanner runs without `--output-*`. `--changed-only` uses Git to list tracked changed files under the requested target but does not include untracked files or mutate files. Treat `--fail-on` nonzero exits as findings status, not scanner crashes, after reading the report. Treat `--input-sarif` as local read-only input that stays in scope and may contain sensitive finding text. Treat `--output-json`, `--output-md`, and `--output-sarif` as write-capable caller-owned review evidence. Skip self-tests/eval suites when they create temp fixtures, temporary Git repos, output files, or execute write-capable commands. Skip local AI, workflow lifecycle, sync without `--check`, publishing, external scanners, installs, credential setup, and implementation fixes unless explicitly approved; report skipped or failed optional setup as non-blocking and continue when core review can proceed.

Scanner mode is for consumer project code; it intentionally skips installed harness and workflow roots such as `.agents`, `.claude`, `.git`, and `automations`. Dogfood the skill itself with docs, help, validation, inspected static evals, and read-only scanner help rather than scanning the skill folder or running temp-writing self-tests. The credentialed risk profile means local secret-like config may be inspected and redacted; it is not permission to configure credentials or read profiles.

## Rules

- Do not upload code, secrets, findings, or dependency data to external services.
- Redact secret values; report locations and evidence shape, not credential contents.
- Do not broaden scope from .NET/ASP.NET review and scanner-supported repo patterns into a generic security audit without explicit user scope.
- Treat JavaScript, TypeScript, Python, Docker, YAML, and Markdown scanner hits as supporting evidence only when they affect the .NET application, build, deployment, documentation, or tool boundary under review.
- Do not mutate source files from scanner mode; write only requested JSON, Markdown, or SARIF evidence outputs.
- Skill manifest outputs stay empty because scanner reports are caller-owned workflow or project evidence, not durable skill-owned artifacts.
- Suppressions require local rationale comments and must remain visible in evidence.

## Validation

Strict no-write validation:

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/dotnet-security-review
python -B .agents/manage.py inspect-skill --skill .agents/skills/dotnet-security-review --fast --summary --compact --format json
python -B .agents/skills/dotnet-security-review/scripts/dotnet_security_review.py --help
python -B .agents/skills/dotnet-security-review/scripts/dotnet_security_review.py scan --help
```

Implementation validation when temp writes are allowed:

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/dotnet-security-review
python -B .agents/skills/dotnet-security-review/scripts/run_self_tests.py
python -B .agents/manage.py eval-skill --skill .agents/skills/dotnet-security-review --suite .agents/skills/dotnet-security-review/suites/dotnet-security-review-evals.json
```

Run self-tests for implementation validation only when temporary fixture writes are allowed. Inspect eval suites first; static assertion suites are read-only, while command/self-test suites are not strict dogfood.

## Stop Rules

Stop before exposing secrets, scanning outside the requested repository or paths, publishing findings, changing auth/crypto/deployment policy, or claiming scanner coverage proves the absence of vulnerabilities.

## Completion Contract

Report scope, selected docs, scanner mode, input paths, output evidence paths, findings by severity, suppressions, skipped files, blocked checks, failed commands, validation result, whether implementation fixes were handed off, and residual security risk.
