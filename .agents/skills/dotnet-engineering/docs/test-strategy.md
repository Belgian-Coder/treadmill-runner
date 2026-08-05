# Test Strategy

Use this file for .NET test design and authoring.

## Choose Test Type

- Unit tests prove domain rules, pure services, validators, serializers, and small control-flow decisions.
- Integration tests prove DI, persistence, HTTP endpoints, auth, serialization, transactions, migrations, and adapters through realistic boundaries.
- End-to-end tests prove user-visible workflows and browser behavior; keep them few, stable, and critical-path focused.
- Prefer fakes over mocks when behavior and state are easier to inspect.

## xUnit And Fixtures

- Keep Facts and Theories focused on one behavior with meaningful data rows.
- Use `IAsyncLifetime` for async setup/teardown that owns real resources.
- Use collection fixtures only for expensive shared resources that can be reset or safely shared.
- Isolate or serialize shared state, ports, databases, files, clocks, and environment variables.

## Integration Boundaries

- Use `WebApplicationFactory` when middleware, routing, filters, auth, serialization, and DI need to run together.
- Use Testcontainers only when real database, broker, or service behavior matters and containers can run.
- Use `DistributedApplicationTestingBuilder` for .NET Aspire topology tests when service discovery, AppHost wiring, or resource readiness is contractual.
- Keep external services behind explicit fakes or local containers; do not call production endpoints from tests.

## Snapshots, UI, And Benchmarks

- Use Verify or equivalent snapshots for API responses, generated files, serializers, and diagnostics; scrub IDs, timestamps, paths, and machine data.
- Do not accept snapshot changes without reviewing the diff and documenting intent.
- Use Playwright for browser flows where DOM, navigation, accessibility, or JavaScript matters; prefer stable selectors and condition-based waits.
- BenchmarkDotNet measures performance, not correctness; use baselines, memory diagnosers, warmup awareness, and environment capture.

## Quality Evidence

- Use Coverlet coverage as a risk signal and Stryker.NET mutation results to find weak assertions.
- Use `dotnet-quality-gates` for parsing TRX/JUnit, coverage, mutation, BenchmarkDotNet, snapshots, and anti-slop evidence.
- Treat flaky candidates as quality risks, not reasons to disable tests.
