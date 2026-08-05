---
name: dotnet-project-context
description: Use when discovering .NET project shape for kickoff, setup, or project-context review, including SDK/runtime signals, solution and project inventory, NuGet/feed policy, validation candidates, and context facts without restore/build/test execution.
---

# Dotnet Project Context

## Goal

Produce read-only `.NET` project facts that improve first-use kickoff and generated project context for business applications.

## Workflow

1. Inspect the narrow project root with the public command:

```shell
python -B .agents/manage.py dotnet-context --target <project-root> --format json
```

Use `--dotnet-executable <path-to-dotnet>` when the project requires a pinned or portable SDK that is not first on `PATH`.
Use `--solution <solution.sln>` and `--project <project.csproj>` to narrow large repositories or monorepos before reviewing validation candidates.

2. Use the report to identify solution/project shape, target frameworks, project classifications, SDK/global.json signals, build policy files, CI dotnet command candidates, repo-local NuGet config, private feed prerequisites, config key inventory, persistence signals, and validation candidates.
3. Treat validation candidates as commands to review, not commands that were executed. This skill does not run restore, build, test, publish, package search/list, package install, tool install, workload install, or analyzers.
4. If `dotnet` is missing, keep the static report and follow the advisory. Missing CLI probes should not block the broader project-context flow.
5. Feed structured facts into `project-context-generator`, `project-context-review`, and `project-kickoff`; keep canonical context review human/agent-confirmed.

## Scope

This skill owns discovery for:

- `.sln`, `.slnx`, `.csproj`, `.fsproj`, and `.vbproj` inventory.
- Optional solution/project filters that narrow the reported inventory without changing the no-restore/no-build policy.
- Static SDK, target framework, output type, nullable, implicit using, warning, package reference, and project reference signals.
- Safe installed CLI probes through `dotnet` on `PATH` or an explicit `--dotnet-executable`: `dotnet --info`, `dotnet sln <solution> list`, and `dotnet msbuild <project> -getProperty/-getItem`.
- Repo-local `NuGet.config`, `Directory.Packages.props`, package source mapping, private-feed detection, redacted source URLs, and credential-section names without values.
- Repo-local `Directory.Build.props`, `Directory.Build.targets`, CI YAML text, appsettings key names, launch profile names, UserSecretsId identifiers, EF/persistence file/package signals, and framework feature signals.
- Candidate restore/build/test commands that callers may review later.
- Optional project-local evidence artifacts under `docs/project/dotnet-context/` when `--write-evidence` is explicit.

## Out Of Scope

- Code editing, Roslyn refactoring, analyzer execution, or migration planning.
- Running restore, build, test, publish, package list/search, package install, tool install, or workload install.
- Reading user/global NuGet config, or reporting credential/configuration values.
- Installing SDKs, packages, tools, workloads, or browser dependencies.
- Replacing `dotnet-engineering`, `dotnet-delivery`, `dotnet-quality-gates`, `dotnet-diagnostics`, or `dotnet-legacy`.

## Extension Point

If CLI/static output proves insufficient, add a later helper deliberately: BCL-only where possible, no default NuGet dependencies, no feed access during normal context generation, and either source-in-repo or self-contained distribution with explicit validation. Do not add a `.csproj`, NuGet package dependency, Roslyn/MSBuild package dependency, or published binary in v1.

## Rules

- Default operation is read-only inspection. The script may read target project files and run only safe `dotnet` probes.
- Do not emit, persist, or log secret values. Report package source credential section names only when present, skip credential-section values, report appsettings key names without values, and redact URL userinfo, query strings, and fragments from reported package source URLs.
- Do not inspect user-level or machine-level NuGet config. Report that they were skipped.
- Do not run commands that can restore packages, contact feeds, create build artifacts, execute tests, install tools, or mutate workloads.
- Do not perform setup or install steps. Missing optional SDK/CLI prerequisites are reported as skipped or advisory items with non-blocking guidance to continue with static facts unless the caller explicitly makes them required.
- Treat `--dotnet-executable` as a trusted local SDK path only; it does not relax the allowed `dotnet` subcommands.
- Treat `--solution` and `--project` as report-scope filters only; they do not grant permission to restore, build, test, or contact feeds.
- Prefer static facts when CLI probes are unavailable, failing, or disabled.
- Report configuration keys and connection-string names only; never emit appsettings values.
- Treat missing or failed optional CLI probes as non-blocking; report them in `skipped`, `advisories`, or `dotnet_cli.probes_failed`.
- Report private/internal feeds as review-required restore prerequisites.
- Keep generated `docs/project/**` facts project-local; they are never promotable back to the harness by default.

## Validation

```shell
python -B .agents/skills/dotnet-project-context/scripts/run_self_tests.py
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/dotnet-project-context
```

## Completion Contract

Report target root, status (`ready`, `partial`, `not-dotnet`, or `blocked`), CLI availability, solution/project counts, private feed status, validation candidates, skipped probes/configs, blocked or failed probes, validation checks run, advisories, and any residual context-review questions.

Report `Skill used: dotnet-project-context - <reason>` when this skill materially affected the work.
