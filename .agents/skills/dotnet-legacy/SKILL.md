---
name: dotnet-legacy
description: Use when maintaining, reviewing, safely refactoring, planning, or executing approved modernization for .NET Framework systems, classic ASP.NET, WCF, WPF/WinForms, old csproj, packages.config, binding redirects, COM/GAC, IIS, or Windows-only constraints.
---

# Dotnet Legacy

## Goal

Handle .NET Framework systems deliberately, separating maintain-in-place fixes from approved modernization or migration work.

## Boundary

Use this skill for .NET Framework and Framework-era surfaces, including SDK-style projects that still target .NET Framework. Use `dotnet-engineering` only when a component is SDK-style and targets modern .NET.

## Workflow

1. Inventory target frameworks, project style, solution layout, build entrypoints, `packages.config`, app/web config, binding redirects, GAC, COM, IIS, Windows services, designer files, and deployment constraints.
2. Classify the request as maintain-in-place, incremental modernization, or migration. Do not start migration when a targeted maintenance fix is enough.
   Approved modernization means explicit user, ticket, or accepted workflow-plan scope for runtime, project-format, package-mode, hosting, or deployment changes.
3. Choose only the needed docs:
   - `docs/build-and-runtime.md` for build tools, reference assemblies, config, and runtime constraints.
   - `docs/maintenance.md` and `docs/framework-patterns.md` for maintain-in-place fixes and safe refactors.
   - `docs/testing-and-validation.md` for compatible test and validation evidence.
   - `docs/ui-and-hosting.md` and `docs/legacy-ui.md` for WPF, WinForms, designer, threading, hosting, and deployment concerns.
   - `docs/framework-assessment.md` and `docs/migration-playbook.md` for approved migration planning.
4. Preserve runtime, project format, package mode, config files, designer files, deployment model, COM/GAC/IIS assumptions, and Windows-only constraints unless explicitly approved.
5. Prefer adapters, strangler paths, and test coverage before broad migration.
6. Use `dotnet-quality-gates` only for compatible evidence; classic projects may require Visual Studio Build Tools, full MSBuild, vstest, IIS Express, or Windows-only prerequisites.

## Read-Only Dogfood

Strict read-only/offline runs may inspect routing Markdown, skill files, manifests, project files, solution files, configs, and docs; run only `module.json.strict_read_only_commands`; and report findings only in the response. Run `scripts/run_self_tests.py` only after inspecting its help/source and confirming it still reads files only. Skip MSBuild, Visual Studio builds, `dotnet build`, `dotnet test`, vstest, restore, NuGet/package updates, config transforms, installer actions, IIS Express starts, Windows service actions, registry/COM/GAC changes, local AI setup, workflow start/finish, evals, sync without `--check`, generated sync/adapters/artifact writes, and raw navigation JSON unless writes, temp state, and network are approved. These commands are write-capable because they may create `bin/`, `obj/`, packages, caches, temp fixtures, test results, config outputs, service state, or validation artifacts. The module risk profile is `local-write` for real legacy maintenance and validation, not for this dogfood path.

Safe command shape: file reads/search, `inspect-skill --fast`, `validate_skill.py`, and self-test only after read-only inspection. Unsafe command shape: restore/build/test/format, project or machine state changes, local AI, workflow lifecycle, write sync, eval suites that execute commands, and any command whose help says it writes reports, caches, trends, packets, or temp fixtures.

## Rules

- Do not assume the `dotnet` CLI can build old .NET Framework projects.
- Do not change framework version, old csproj format, `packages.config`, binding redirects, app/web config, installer behavior, or deployment targets without an approved modernization task.
- Keep compatibility notes concrete: target framework, host, package manager, build tool, deployment path, and test runner.
- Preserve service accounts, registry, filesystem ACL, certificate store, machine-level config, COM registration, and GAC dependencies until proven unnecessary.
- Keep legacy UI designer and threading constraints explicit.

## Validation

Use repository-documented commands when writes/network are acceptable. For changes to this skill itself, run `python -B .agents/skills/dotnet-legacy/scripts/run_self_tests.py`. If project commands are absent, identify likely Visual Studio/MSBuild/vstest commands but do not invent a green build. For strict read-only work, skip build/test/restore and state why. Validate config diffs, binding redirects, package restore, designer integrity, deployment assumptions, and compatible test evidence when touched.

## Stop Rules

Stop before changing runtime version, project format, package manager, COM/GAC registration, IIS configuration, database migrations, installer behavior, Windows service deployment, or production deployment assumptions. Stop when required Windows tooling is missing and no existing evidence can prove the change.

## Completion Contract

Report inventory signals, classification, selected docs, changed paths, commands run, generated artifacts, validation result, optional setup or tooling skipped/failed, whether it is non-blocking, whether work can continue, skipped or blocked checks, failed command summaries, and residual legacy or modernization risk.
