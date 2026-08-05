---
name: dotnet-engineering
description: Use when building, reviewing, refactoring, or architecting modern .NET/C# applications, libraries, ASP.NET Core services, EF Core data access, Blazor, MAUI, WinUI, modern WPF/WinForms, AI integration code, document-generation code, background work, tests, performance-sensitive code, or delivery pipelines. Use dotnet-project-context for kickoff/setup/project-context inventory, dotnet-diagnostics for runtime evidence work, dotnet-legacy for .NET Framework work, dotnet-security-review for security risk, and dotnet-quality-gates for validation evidence.
---

# Dotnet Engineering

## Goal

Make modern .NET changes that fit the repository, preserve deterministic validation, and avoid mismatched framework assumptions.

## Workflow

1. Inspect solution, projects, target frameworks, packages, build scripts, tests, and deployment hints before changing code. For first-use kickoff, setup, or generated project context, switch to `dotnet-project-context` before engineering changes.
2. For crashes, hangs, dumps, traces, counters, live attach, or runtime evidence, switch to `dotnet-diagnostics` before code changes.
3. For .NET Framework, old non-SDK projects, `packages.config`, Web Forms, classic ASP.NET MVC, WCF, GAC, COM, IIS-only assumptions, maintain-in-place WPF/WinForms, or approved migration, switch to `dotnet-legacy`.
4. Choose only docs for the touched area:
   - Core: `docs/csharp-runtime.md` (async, DI, LINQ, serialization, concurrency, performance), `docs/runtime-tooling.md` (source generation, GeneratedRegex, LoggerMessage, Roslyn, interop), `docs/csharp-foundations.md` (nullable, options, DataAnnotations, strings, dates, paths, globalization).
   - Backend: `docs/architecture.md` (Clean Architecture, slices, API compatibility, domain modeling, analyzers), `docs/aspnet-backend.md` (ASP.NET Core, EF Core, middleware, background work, HTTP clients, HybridCache, OpenAPI, auth), `docs/data-and-integration.md` (outbox/inbox, idempotency, SignalR, SSE, gRPC), `docs/api-contracts.md` (versioning, SemVer, PublicApiAnalyzers, package validation).
   - UI/output: `docs/ui.md` (Blazor, MAUI, WinUI, WPF/WinForms, accessibility), `docs/ui-frameworks.md` (render modes, QuickGrid, MAUI Shell, Native AOT, ARIA, localization), `docs/openxml-and-pdf.md` (Open XML SDK, ClosedXML, EPPlus, PDFsharp, templates, fonts).
   - Tests/delivery/docs: `docs/testing.md` (test pyramid, integration/browser/snapshot/mutation testing, BenchmarkDotNet), `docs/test-strategy.md` (xUnit, `IAsyncLifetime`, WebApplicationFactory, Testcontainers, Aspire, Verify, Playwright, coverage, Stryker.NET), `docs/delivery.md` (CI, containers, NuGet, releases, OpenTelemetry), `docs/documentation.md` (XML docs, README, Mermaid, DocFX).
5. Implement in the local project style. Prefer small changes with tests over broad rewrites.
6. For strict read-only inventory, omit output flags and print evidence only:

```shell
python -B .agents/skills/dotnet-engineering/scripts/dotnet_repo_inspector.py all --target <project-root> --target-version <netX.Y> --format markdown
```

7. For migration, upgrade, or package-heavy work that allows artifacts, add explicit `--output-json`/`--output-md` paths in project-owned validation folders.
8. For AI integration code, stay at stable boundaries: service registration, provider selection, prompt/template rendering, tool/function authorization, retrieval filters, cancellation/timeouts, token limits, structured outputs, deterministic fallback tests. Use `local-ai-helper` for repo-local model/runtime setup and `dotnet-security-review` for prompt-injection, secret, customer-data, or production-action risk.
9. Validate with documented project commands first. Use `dotnet-quality-gates` for normalized evidence and `playwright-integration` only for browser readiness.

## Read-Only Dogfood

Strict read-only/offline runs may inspect routing, skill files, manifests, and project files; use `inspect-skill --fast`; run only `module.json.strict_read_only_commands`; and report findings only in the response. `dotnet_repo_inspector.py` is strict-safe only without `--output-json` or `--output-md`. Skip `run_self_tests.py` when no temp fixtures are allowed; it writes temporary fixture projects. Skip `dotnet build`, `dotnet test`, `dotnet format`, restore, analyzers, CI scripts, code generation, migrations, package/container dry-runs, local AI setup, cloud CLIs, workflow start/finish, sync, and generated routing/adapters unless writes and network are approved. These commands are write-capable because they may create `bin/`, `obj/`, caches, test results, formatted source, validation artifacts, or package downloads.

When offline, do not perform current official docs lookup; report it as skipped or rely only on repo-local docs already present.

## Rules

- Prefer project-native commands, analyzers, package versions, and architecture boundaries.
- Do not install SDKs, retarget frameworks, change package management, publish artifacts, or add services unless explicitly requested.
- For changing .NET libraries, check current official docs when exact syntax, packages, or platform support matters and network access is allowed.
- Keep generated files, build output, and validation artifacts in existing project-owned locations.
- Preserve cancellation, logging, error handling, and dependency injection conventions unless they are the defect.
- Keep modern guidance separate from legacy modernization. Do not retrofit .NET Framework advice into new .NET work.
- For AI packages/orchestration libraries, check current official docs before changing exact package names, experimental APIs, or provider syntax when network access is allowed; keep this skill focused on project code, not model/runtime installation.
- Treat `dotnet_repo_inspector.py` output as inventory/planning evidence, not proof that restore, build, or migration passes.

## Validation

Run the narrowest deterministic proof: `dotnet test`, `dotnet build`, `dotnet format --verify-no-changes`, analyzers, or CI scripts when writes/network are acceptable. For strict read-only work, skip them and state why. For this skill's source changes, `run_self_tests.py` is allowed only when system-temp fixtures are acceptable. For AI integration, prefer fakes, recorded responses, contract tests, and retrieval/prompt rendering tests over live model calls. Reuse fresh test, coverage, mutation, benchmark, and snapshot artifacts.

## Stop Rules

Stop before changing runtime versions, package management mode, deployment targets, secrets, production data access, live external AI services, embedding storage for sensitive content, production model-callable actions, or destructive build cleanup. Stop when required SDKs/workloads are missing and no existing evidence can prove the change.

## Completion Contract

Report inspected project signals, chosen docs, changed paths, commands, generated artifacts, validation, optional setup/tooling skipped or failed, non-blocking status, whether work can continue, skipped/blocked checks, failed-command summaries, and risk.
