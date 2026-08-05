# Data And Integration

Use for modern .NET data access, persistence boundaries, service communication, messaging, and file-backed app choices.

## Data Access Choice

- Use EF Core for rich domain models, change tracking, migrations, and LINQ-shaped persistence.
- Use Dapper or raw ADO.NET for narrow read models, SQL-first tuning, or hot paths with explicit query shape.
- Do not add repositories just to hide EF Core; use them when they express aggregate load/save semantics or protect the domain from `IQueryable<T>` leakage.
- Keep DTO/query projections separate from write aggregates when read shape, performance, or API contract differs from the domain.
- For file-backed or local-first apps, decide durability, locking, backup, concurrent writers, and migrations before choosing SQLite, LiteDB, JSON files, or custom storage.

## EF Core Boundaries

- Keep `DbContext` short-lived: scoped per web request/unit of work; use `IDbContextFactory<T>` for background services, Blazor Server circuits, and long-lived owners.
- Use `AsNoTracking` for read-only queries/projections; keep tracking when the same context updates returned entities.
- Use `AsSplitQuery` or projections when multiple collection includes risk cartesian explosion; document when atomic single-query behavior matters more.
- Treat migrations as deployment artifacts. Prefer reviewed idempotent scripts or bundles for production; avoid startup migration/schema creation unless deployment owns that mutation.
- Use `SaveChangesInterceptor` for audit timestamps, soft delete, domain event capture, or outbox writes when it keeps entities cleaner.
- Use compiled queries only for measured repeated hot shapes; keep normal async LINQ when cancellation or query variation matters.

## Transactions And Messaging

- Make transaction boundaries explicit around multi-step state changes. If retry strategies rerun delegates, require idempotency or database uniqueness.
- For outbound messages from database changes, use a transactional outbox so state and intent commit together.
- For inbound messages, use an inbox/processed-message table plus idempotency keys so repeated delivery does not repeat side effects.
- Separate domain events from integration events. Domain events may use in-process domain types; integration events use stable DTO/primitive payloads.
- Use durable queues/brokers for work that must survive restarts; in-memory channels are best-effort or short-lived local coordination.

## Service Communication

- Use REST or Minimal APIs for broad HTTP compatibility and human-debuggable JSON.
- Use gRPC with Protobuf for typed service contracts, high throughput, and streaming when clients support HTTP/2.
- Treat `.proto` files as contracts and set `GrpcServices` deliberately on `Protobuf` items.
- Use SignalR for browser-compatible bidirectional realtime; use Server-Sent Events for simple server-to-client push.
- Map errors into native contracts: HTTP `ProblemDetails`, gRPC status codes, and explicit reconnect/backoff for realtime channels.
- Record load balancing, browser support, auth, cancellation, deadlines, payload shape, and observability trade-offs before switching protocols.

## Review Checklist

- Data choice matches query complexity, write consistency, deployment model, and team conventions.
- EF lifetime, tracking, split-query, migration, interceptor, and compiled-query decisions are intentional.
- Messaging has outbox/inbox or another idempotency story when delivery can repeat.
- Realtime or service-to-service protocol matches clients, streaming direction, payload size, and operations constraints.
