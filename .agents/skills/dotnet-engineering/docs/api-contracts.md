# API Contracts

Use when modern .NET work changes public library APIs, HTTP API versions, generated API docs, or consumer migration expectations.

## Classify The Contract

- Binary compatibility: compiled consumers still load/run. Removing public types or members, changing return/parameter types, changing struct layout, sealing extension points, or moving types without forwarding can break runtime load/dispatch.
- Source compatibility: consumers can recompile without errors or silent rebinding. New overloads, parameter-name/default changes, optional parameters, `params`, and extension methods can be source-breaking.
- Wire compatibility: serialized names, status codes, routes, headers, event payloads, and OpenAPI contracts preserve client expectations.
- .NET Framework binding redirects, GAC, COM, classic WCF, Web Forms, and old csproj projects belong to `dotnet-legacy`.

## Public Library Surface

- Map API impact to SemVer before editing: binary-breaking removals/signatures need planned major versions; additive public surface usually minor; internal-only fixes are patch candidates.
- Prefer additive APIs over signature changes. Keep obsolete members until planned removal, and include replacement API plus removal version in `[Obsolete]`.
- Check overload source compatibility, especially literals, `null`, optional parameters, `params`, and implicit conversions.
- Use type forwarders when moving public types; place forwarding in the original assembly and consider `[TypeForwardedFrom]` for serialized assembly-qualified types.
- Dropping a target framework in a multi-targeted package is a compatibility break.

## Deterministic Gates

- Use PublicApiAnalyzers when explicit public-surface review matters. Keep `PublicAPI.Shipped.txt` and `PublicAPI.Unshipped.txt`, include `#nullable enable`, and move unshipped entries only at release time.
- Use `EnablePackageValidation` when a packable project owns NuGet release discipline.
- Keep `ApiCompatSuppressionFile` as an `ItemGroup` item and review generated suppressions before commit.
- Snapshot API tests can supplement, not replace, build-time API tracking and package validation.
- Use `dotnet-quality-gates` for normalized scanner evidence or repo-specific API-contract checks.

## HTTP Versions And Docs

- Preserve existing versioning unless the task changes it. For new public ASP.NET Core APIs, prefer a visible strategy, usually URL segments, unless client/gateway/cache constraints justify headers or query strings.
- Require explicit version selection; default fallback is only for controlled compatibility migrations.
- Deprecate versions with sunset date, response metadata, migration link, and owned replacement path.
- Check current official package guidance before adding or replacing `Asp.Versioning` packages or OpenAPI versioning setup.
- Keep XML docs, DocFX/API reference, OpenAPI, changelogs, and migration guides synchronized with shipped contracts.
- Breaking-change docs list removed/changed APIs, replacements, behavioral changes, required dependency/config changes, and useful before/after examples.
- Preserve stable versioned-doc URLs or redirects; do not expose interactive OpenAPI UIs in production without an approved security posture.
