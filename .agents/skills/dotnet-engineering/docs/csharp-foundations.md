# C# Foundations

Use for modern C# changes touching defaults, nullability, configuration, text/time, file I/O, or input validation.

## Nullable Contracts

- Check the project `Nullable` setting before changing annotations; existing projects may differ from modern templates.
- Do not silence warnings with `!` unless a local invariant is hidden from the compiler. Prefer explicit null handling, guards, or contract attributes.
- Preserve interface and override nullability; a non-null implementation of a nullable contract changes caller expectations.
- Use `System.Diagnostics.CodeAnalysis` attributes such as `NotNullWhen`, `MemberNotNull`, and `NotNullIfNotNull` for try-patterns, initialization, and pass-through helpers.

## Configuration And Options

- Inspect configuration source order before adding sources; later sources override earlier JSON, environment, user secret, and command-line values.
- Keep options binder-friendly with simple public setters unless the project already uses constructor binding.
- Use options validation for startup-critical settings and `ValidateOnStart` when invalid config should fail fast.
- Use `IOptions<T>` for static settings, `IOptionsSnapshot<T>` for request/scope refresh, and `IOptionsMonitor<T>` for singleton/background live reload.
- Treat user secrets as development-only; production secrets belong in environment or existing vault infrastructure.

## Globalization And Time

- Specify `StringComparison` for identifiers, tokens, headers, paths, feature flags, and protocols.
- Use ordinal comparison for programmatic identifiers; use current or explicit culture for user-visible sort/search and display/input.
- Use `CultureInfo.InvariantCulture` for persisted or transmitted numeric/date text.
- Prefer `DateTimeOffset` for timestamps crossing process, API, database, or log boundaries; use `DateOnly`/`TimeOnly` for date/time-only domains.
- Store portable time zone IDs deliberately and validate platform expectations; do not assume server-local time matches the user.
- Use `StringInfo` or Unicode-aware APIs for visible text truncation/counting; `char` is a UTF-16 code unit.

## File I/O

- For true async file I/O, open `FileStream` with `useAsync: true` or `FileOptions.Asynchronous`.
- Use `RandomAccess` for offset-based concurrent reads/writes and `FileStream` for ordinary sequential stream integration.
- Treat `FileSystemWatcher` as hints: debounce duplicates, handle `Error`, and rescan after overflow or missed events.
- For untrusted names, do not trust raw `Path.Combine`; join, normalize with `GetFullPath`, then prove the result stays under the intended base directory.
- Create temp files with random names and atomic `CreateNew`; handle permissions, sharing violations, disk full, and flush/dispose failures.

## Input Validation

- Keep transport validation at the boundary and domain invariants inside the domain model or use case.
- Pick one validation approach per request model: DataAnnotations for simple DTOs, FluentValidation/project validators for conditional, cross-field, or dependency-aware rules.
- Use `IValidateOptions<T>` for configuration invariants and actionable startup/scope errors.
- Use `Validator.TryValidateObject` only for explicit manual validation; it is not recursive unless code walks nested objects.
- Do not add validation packages or source-generator attributes from memory; check official docs when package names, target support, or Minimal API/controller integration matters.
- Return validation failures in the project-standard shape, usually `ProblemDetails` or `ValidationProblemDetails`.
- Normalize before validation only when the domain requires it; keep security checks allowlist-oriented for paths, URLs, identifiers, commands, and file names.
