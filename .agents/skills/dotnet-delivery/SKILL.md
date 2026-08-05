---
name: dotnet-delivery
description: Use when designing, reviewing, or preparing .NET CI/CD, GitHub Actions, Azure DevOps pipelines, build/test/publish stages, NuGet packaging, container images, MSIX packaging, release management, environment approvals, service connections, OpenTelemetry, health checks, or structured logging. Use dotnet-quality-gates for normalized validation evidence.
---

# Dotnet Delivery

## Goal

Prepare .NET delivery changes that preserve deterministic build evidence, release safety, package integrity, container hygiene, observability, and credential boundaries without publishing or deploying implicitly.

## Read-Only Dogfood

Strict read-only/offline review uses routing, file inspection, `inspect-skill --fast`, and only `module.json.strict_read_only_commands`. This skill's `run_self_tests.py` is a read-only self-test exception because it only reads local skill files, sets `sys.dont_write_bytecode = True`, and does not create temp files, subprocesses, network calls, or outputs. Skip official docs lookup, cloud CLIs/APIs, local AI, build/test/format/pack/publish, Docker builds, package/container dry-runs, and `dotnet-quality-gates` commands unless they only parse existing evidence without output flags and the task allows that scope.

## Workflow

1. Inventory provider, pipeline files, `global.json`, target frameworks, package mode, central package management, test projects, artifact paths, release targets, credentials, service connections, and environment approvals.
2. Use `docs/ci-packaging-release.md` for GitHub Actions, Azure DevOps, setup-dotnet, DotNetCoreCLI, NuGet cache, multi-stage Dockerfiles, rootless containers, SBOM, NuGet authoring, MSIX, SemVer, NBGV, OpenTelemetry, health checks, structured logging, and PII controls.
3. Use `dotnet-quality-gates` for parsing or producing normalized test, coverage, mutation, benchmark, snapshot, package, and scanner evidence. In strict dogfood, use it only for existing-evidence parsing without output flags; skip commands that run project tools or write reports.
4. Keep application code changes in `dotnet-engineering`, security review in `dotnet-security-review`, and runtime evidence in `dotnet-diagnostics`.
5. Validate locally or in dry-run form before proposing remote pipeline behavior. Check current official docs when action/task syntax, package names, or platform support matters; when offline, report that docs verification was skipped.

## Rules

- Do not publish packages, push images, deploy infrastructure, create releases, sign artifacts, call cloud CLIs/APIs for live settings, or modify service connections unless the user explicitly approves that boundary.
- Do not weaken warnings, NuGet audit, analyzers, tests, package-source mapping, or credential handling to get a green run.
- Do not add secrets to YAML, Dockerfiles, package sources, scripts, logs, or default configuration.
- Preserve existing CI provider, branching model, artifact layout, package versioning, and release gates unless the task explicitly changes them.
- Separate restore, build, test, pack, publish, deploy, and promotion stages so failures produce useful evidence.

## Validation

For strict read-only, run only deterministic inspection and skill checks. For implementation work, run available local commands first, but treat repo build/test/format, package/container dry-runs, and `dotnet-quality-gates` outputs as write-capable because they may create `bin/`, `obj/`, packages, caches, or evidence files. For this repo, `sync --check` is read-only. For changes to this skill itself, `python -B .agents/skills/dotnet-delivery/scripts/run_self_tests.py` is the read-only self-test exception because it only reads local skill files. If remote-only validation or docs lookup is skipped or fails, report whether work can continue from local evidence.

## Stop Rules

Stop before remote uploads, deployment, package push, container push, signing, production environment changes, service connection edits, credential creation, or release creation. Stop if a proposed delivery change requires secrets or hosted permissions that are unavailable.

## Completion Contract

Report inspected delivery files, selected docs, changed paths, commands run, generated artifacts, validation result, remote actions skipped/performed, credentials or service connections touched, optional setup skipped/failed, whether it is non-blocking, whether work can continue, skipped or blocked checks, failed command summaries, and residual release risk.
