# C# Runtime

Use for modern C# libraries, workers, shared runtime code, and application services.

## Project Signals

- Confirm target frameworks, language version, nullable mode, implicit usings, analyzers, warnings-as-errors, central package management, trimming, and AOT flags before using new syntax or APIs.
- Prefer existing abstractions unless they hide cancellation, disposal, logging, or error boundaries.

## Async, Cancellation, I/O

- Propagate `CancellationToken` through public async paths, I/O, EF, HTTP, channels, and background work.
- Avoid sync-over-async, `Task.Run` wrappers around naturally async work, and arbitrary sleeps/delays without stopping tokens.
- Use `Task<T>` by default. Use `ValueTask<T>` only for measured hot paths that complete synchronously most of the time; never await it twice or read `.Result` before completion.
- Use `ConfigureAwait(false)` for reusable libraries that may run under a synchronization context; avoid it in UI continuations that update controls.
- Use `IAsyncEnumerable<T>` only when streaming has value; otherwise return materialized results with clear ownership.
- Prefer `Stream` for ordinary I/O. Use `System.IO.Pipelines` only when throughput, backpressure, and buffer ownership justify complexity.
- For pipelines, pair `PipeReader.ReadAsync` with `AdvanceTo`, stop using buffers after advancing, respect `PipeWriter.FlushAsync` backpressure, and prefer `SequenceReader<byte>` for fragmented protocol parsing.

## Dependency Injection And Options

- Pick lifetimes deliberately: singleton for stateless/thread-safe, scoped for request or unit-of-work state, transient for cheap stateless dependencies.
- Avoid captive dependencies; singletons must not capture scoped services. Create scopes only at explicit, tested boundaries.
- Use keyed services only when the key represents a real runtime choice; centralize keys enough to avoid stringly typed call sites.
- Use `TryAdd` for library defaults and options validation for startup-critical configuration.
- Avoid `BuildServiceProvider` during registration except for rare documented framework integration.

## LINQ, Concurrency, Generation

- Keep query filters/projections server-side until the intended materialization boundary; treat early `AsEnumerable()` as a risk.
- Make expensive materialization, buffering, caching, and multiple enumeration explicit. Prefer keyset pagination for large stable ordered sets.
- Use `Channel<T>`, `ConcurrentDictionary`, locks, and queues only with ownership and backpressure rules.
- Prefer bounded channels and deliberate `BoundedChannelFullMode`; consumers use `WaitToReadAsync`/`TryRead` or `ReadAllAsync` with cancellation and completed writers.
- Avoid check-then-act on concurrent collections, locking externally visible objects, and `await` inside `lock`; use `SemaphoreSlim.WaitAsync` for async mutual exclusion.
- Treat `ConcurrentDictionary.GetOrAdd` delegates as possibly repeated under contention; avoid side effects or wrap expensive creation in `Lazy<T>`.
- For trimmed, AOT, or high-throughput paths, prefer source-generated `System.Text.Json` contexts and compiler-validated generated regex/logging/serialization members.

## Review Checklist

- Cancellation, disposal/async disposal, event unsubscribe, async event/error routes, and background shutdown are explicit.
- Logging templates are structured, exceptions preserve context and stack traces with `throw;`, and failures are not swallowed.
- Hot paths avoid needless allocation, reflection, repeated parsing, starvation-causing sync work, and unmeasured thread-pool tuning.
