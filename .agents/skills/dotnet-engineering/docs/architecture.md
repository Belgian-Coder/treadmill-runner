# Architecture

Use for modern .NET project structure, architecture review, and framework trade-offs before implementation details dominate.

## Context First

- Identify target frameworks, app model, deployment host, package mode, project references, data stores, external systems, and current style.
- Ground recommendations in detected constraints; do not assume latest SDK, cloud host, UI framework, or package stack.
- Keep simple CRUD services simple. Do not add Clean Architecture layers, mediators, or abstractions for low-rule or short-lived systems.

## Boundaries

- Clean Architecture fits long-lived apps with significant domain rules, multiple external dependencies, and testable workflows.
- Vertical slices fit feature-oriented services where request handling, validation, data access, and tests stay cohesive.
- If layering exists, dependency direction must be visible: domain has no outer references, application owns use cases/interfaces, infrastructure implements adapters, and web/API stays at the edge.
- Keep business logic out of controllers, pages, components, middleware, transport EF entities, and hosted-service loops.
- Prefer existing boundaries unless they hide validation, cancellation, transactions, security, or observability.

## API And Domain Design

- Treat public API compatibility as a constraint: clear names, stable wire names, overloads or optional members before breaking required parameters.
- Order parameters as target/subject, required inputs, optional inputs, then `CancellationToken`.
- Return read-only collections for materialized data and explicit streaming types for lazy/unbounded results.
- Use aggregate roots, value objects, and domain events only when business rules justify the structure.
- Keep aggregate mutation behind invariant-enforcing methods; translate domain events to DTO/primitive integration events at external boundaries.
- Repositories express aggregate load/save semantics and should not leak `IQueryable<T>` from the domain.

## Types, Analyzers, Trade-Offs

- Choose `class`, `record`, `readonly struct`, `record struct`, `Span<T>`, or `Memory<T>` from identity, immutability, and lifetime needs.
- Do not store `Span<T>` or cross `await`; use `Memory<T>` for async or longer-lived buffers.
- Seal public library types unless inheritance is an intentional documented extension point.
- Preserve EditorConfig, `AnalysisLevel`, generated-code exclusions, and test-code relaxations unless policy is in scope.
- Minimal APIs fit small services, AOT-sensitive APIs, and endpoint groups with explicit filters; controllers remain valid for large existing APIs or convention-heavy MVC.
- gRPC, SignalR, background services, Blazor, MAUI, WinUI, WPF, Uno, Avalonia, Native AOT, trimming, startup, containers, mobile, and browser targets are project-context decisions.

## Review Severity

- Critical: concrete correctness, security, data loss, production mutation, auth, unsafe migration, deadlock, or deployment breakage.
- Warning: likely future defect from maintainability, architecture drift, performance risk, missing validation/cancellation/tests, or observability gaps.
- Suggestion: style, naming, simplification, or optional modernization with low risk.

## Review Checklist

- Architecture follows project constraints, dependency direction, and package/project references.
- Validation, authorization, transactions, retries, logging, metrics, configuration, and error contracts have clear owners.
- API/domain changes preserve caller, wire, analyzer, and persistence contracts unless breaking change is explicit.
- Tests cover the highest-risk boundary: domain rules, endpoint behavior, persistence, adapters, or UI flow.
- Findings cite file evidence and separate observed facts from inference.
