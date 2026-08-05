# ASP.NET Backend

Use for ASP.NET Core APIs, services, middleware, data access, background work, and web-hosted infrastructure.

## Endpoint Architecture

- Keep transport at the edge and business rules in testable services.
- Prefer `TypedResults`, explicit status codes, and consistent error contracts so OpenAPI metadata and tests prove response shape.
- Use `ProblemDetails` or `ValidationProblemDetails` instead of ad hoc anonymous error objects.
- Keep route groups, filters, and controllers aligned with existing style.
- Use endpoint filters for endpoint-scoped Minimal API concerns; use global middleware only for whole-pipeline concerns.
- Treat endpoint filter order as behavior: validation should run before idempotency or caching so invalid requests are not stored or replayed.

## Middleware

- Verify ordering for routing, CORS, authentication, authorization, rate limiting, output caching, reverse proxies, static files, and exception handling.
- Prefer endpoint or route-group policy when global middleware is unnecessary.
- Keep development-only middleware behind environment checks.
- Use `IMiddleware` when reusable middleware needs scoped dependencies; convention middleware instances are created once and must not capture scoped services.
- Prefer `UseExceptionHandler` and centralized exception mapping for production errors.
- For YARP, prove auth/rate-limit ordering before `MapReverseProxy` and keep route/cluster config ownership explicit.

## EF Core

- Keep query filters, projection, paging, includes, tracking, and materialization intentional.
- Avoid startup migrations or schema creation in production unless deployment explicitly owns it.
- Use transactions for multi-step state changes, scoped DbContext lifetimes, and cancellation on async calls.

## HTTP, Caching, OpenAPI

- Use `IHttpClientFactory` or typed clients; avoid repeated `new HttpClient()` and make `BaseAddress` slash behavior explicit.
- Preserve the current resilience stack; do not mix legacy Polly handlers with standard resilience handlers unless documented.
- Check current official package guidance before introducing or replacing resilience packages.
- Separate response/output caching from data caching.
- Use `HybridCache` only when framework/packages support it and key, TTL, stampede, serialization, and invalidation behavior are explicit.
- Prefer `HybridCache.GetOrCreateAsync` for stampede protection; avoid get-then-set for the same key.
- Preserve the existing OpenAPI stack; do not mix Swashbuckle, NSwag, and first-party OpenAPI packages without a migration plan.
- Align new endpoint metadata with `TypedResults`, route names, auth policies, validation errors, and versioning.

## Background Work And Idempotency

- Use `BackgroundService` with stopping tokens, bounded queues, logging, and clear failure handling.
- Use `IHostedService` for startup/shutdown hooks, not durable polling loops.
- Avoid unbounded in-memory queues, startup migrations, cache warming, or external calls that block startup unless ownership and timeout behavior are explicit.
- For retryable writes, scope `Idempotency-Key` by route plus user/tenant, distinguish no record/in-progress/completed, replay only concrete response envelopes, and finalize records for all `IResult` subtypes.

## Security, Observability, Review

- Keep auth and authorization decisions close to endpoint ownership.
- Avoid permissive CORS, insecure cookies, raw request body logging, and secret-bearing mutable options.
- Emit structured logs and metrics at system boundaries.
- Review middleware order, cancellable EF Core work, current resilience packages, caching/OpenAPI/proxy/hosted-service fit, hidden production mutation, and endpoint/service tests.
