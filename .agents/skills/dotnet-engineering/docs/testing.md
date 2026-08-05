# Testing

Use this file when adding or reviewing modern .NET tests.

## Test Shape

- Keep the test pyramid intentional: many cheap unit tests, fewer integration tests for real boundaries, and only critical-path E2E tests.
- Unit tests prove pure behavior, validators, serializers, small services, and control flow with real code where practical.
- Integration tests prove DI, database mapping, middleware, auth, serialization, endpoints, and transactions.
- Browser tests prove user-visible workflows only; setup stays with the web project or `playwright-integration`.
- Contract tests cover service request/response compatibility when a full environment is too slow or brittle.

## Common Tools

- Follow the existing xUnit, NUnit, or MSTest style.
- Use `WebApplicationFactory` for ASP.NET Core hosting, middleware, routes, auth, filters, and serialization.
- Use Testcontainers or local fixtures only when realistic dependencies matter.
- Use snapshots for API responses, generated files, serializers, and diagnostics when received and approved artifacts are reviewable.

## Determinism

- Avoid sleeps, wall-clock assertions, random ports without isolation, and shared mutable fixtures.
- Inject time, randomness, environment, and external services.
- Seed fake data; isolate files, ports, databases, clocks, and environment variables.
- Keep fixture lifetimes visible under parallel execution and output stable enough for cheap comparisons.
- Mark skipped tests with clear prerequisites, not vague environment notes.

## Benchmarks

- Use BenchmarkDotNet for throughput, latency, allocation, GC behavior, or before/after claims with explicit baselines.
- Use realistic `[Params]`, setup/cleanup outside the measured region, and recorded runtime, TFM, and environment.
- Prevent dead code elimination by returning or consuming computed values; avoid constant folding by creating inputs during setup.
- Use `[MemoryDiagnoser]` for allocation claims; avoid `[ShortRunJob]` for publishable evidence.

## Coverage And Mutation

- Treat coverage as a gap finder.
- Use mutation reports only when the project owns the tool or the workflow asked for it.
- Prefer focused tests for branches that previously failed validation or were easy for agents to miss.

## Review Gate

- The test would fail for the target defect.
- Assertions prove behavior, fixtures isolate shared resources, and failure messages are actionable.
- Long-running or flaky tests stay outside cheap deterministic gates.
